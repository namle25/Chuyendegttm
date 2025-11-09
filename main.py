import cv2
import json
import os
import numpy as np
from ultralytics import YOLO
import cvzone
from collections import deque
import runtime_status
import email_service
# Load YOLOv8 model
model = YOLO('best.pt')
names = model.names

cap = cv2.VideoCapture("71.mp4")
frame_count = 0

# Detection config - tweak to improve accuracy
CONF_THRESHOLD = 0.35        # minimum model confidence to consider a detection
OVERLAP_THRESHOLD = 0.2      # fraction of box area overlapping a polygon to mark occupied
SKIP_FRAMES = 0              # number of frames to skip between processed frames (0 = process every frame)

# Temporal smoothing for occupancy (reduce flicker)
OCCUPANCY_CONFIRM_FRAMES = 3   # need this many consecutive positive frames to mark occupied
OCCUPANCY_RELEASE_FRAMES = 2   # need this many consecutive negative frames to mark free

# Box smoothing per track id
BOX_SMOOTHING_FRAMES = 3  # running average over this many detections per tracked id
track_buffers = {}  # track_id -> deque of (x1,y1,x2,y2,conf)

# Obstacle detection settings
ANY_CLASS_AS_OBSTACLE = False  # if True, any detected object will count as obstacle
MIN_BOX_AREA = 300  # ignore detections smaller than this area (in pixels)


# allowed object classes to consider as vehicles
VEHICLE_CLASS_NAMES = {'car', 'truck', 'bus', 'motorcycle', 'bicycle'}
allowed_class_ids = [i for i, n in (names.items() if isinstance(names, dict) else enumerate(names)) if (n in VEHICLE_CLASS_NAMES)]

# Polygon drawing variables
polygon_points = []
polygons = []  # List of polygons (each is a list of 4 points)
polygon_file = "polygons.json"

# Load saved polygons safely
if os.path.exists(polygon_file):
    try:
        with open(polygon_file, 'r') as f:
            polygons = json.load(f)
    except (json.JSONDecodeError, ValueError):
        print("Warning: polygons.json is empty or corrupted. Resetting.")
        polygons = []
        with open(polygon_file, 'w') as f:
            json.dump(polygons, f)

# Save polygons to JSON
def save_polygons():
    with open(polygon_file, 'w') as f:
        json.dump(polygons, f)

# occupancy counters per polygon (temporal smoothing)
occupancy_counters = []
# initialize counters if polygons file had entries
if polygons:
    occupancy_counters = [0] * len(polygons)

# Mouse callback to add polygon points
def RGB(event, x, y, flags, param):
    global polygon_points, polygons
    if event == cv2.EVENT_LBUTTONDOWN:
        polygon_points.append((x, y))
        if len(polygon_points) == 4:
            polygons.append(polygon_points.copy())
            # keep occupancy_counters in sync
            occupancy_counters.append(0)
            save_polygons()
            polygon_points.clear()

cv2.namedWindow("RGB")
cv2.setMouseCallback("RGB", RGB)

# Video/frame selection controls
total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
use_trackbar = total_frames > 0
if use_trackbar:
    def on_trackbar(pos):
        # Seek to frame when user moves trackbar
        cap.set(cv2.CAP_PROP_POS_FRAMES, pos)

    cv2.createTrackbar('Frame', 'RGB', 0, max(0, total_frames - 1), on_trackbar)

# Playback control
playing = True
last_trackbar_pos = -1
# start email responder thread if enabled
email_service.start_if_enabled()
while True:
    # If trackbar is used, follow its position when paused/seeked (guarded)
    if use_trackbar:
        try:
            pos = cv2.getTrackbarPos('Frame', 'RGB')
            if pos != last_trackbar_pos:
                cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
                last_trackbar_pos = pos
        except Exception:
            # if trackbar/window not available, disable to avoid crash
            use_trackbar = False

    ret, frame = cap.read()
    if not ret:
        break

    frame_count += 1
    # frame skipping configurable: if SKIP_FRAMES > 0 and playing, skip frames for speed
    if SKIP_FRAMES > 0 and (frame_count % (SKIP_FRAMES + 1) != 0) and playing:
        continue

    frame = cv2.resize(frame, (1020, 500))
    results = model.track(frame, persist=True)

    # Draw saved polygons
    for poly in polygons:
        pts = np.array(poly, np.int32).reshape((-1, 1, 2))
        cv2.polylines(frame, [pts], isClosed=True, color=(0, 255, 0), thickness=2)

    # Track how many zones are occupied (use overlap-based check)
    occupied_zones = 0
    if results[0].boxes.id is not None:
        ids = results[0].boxes.id.cpu().numpy().astype(int)
        boxes = results[0].boxes.xyxy.cpu().numpy().astype(int)
        class_ids = results[0].boxes.cls.int().cpu().numpy().astype(int)
        confs = results[0].boxes.conf.cpu().numpy()

        # precompute polygon masks at current frame resolution
        poly_masks = []
        h, w = frame.shape[:2]
        for poly in polygons:
            pm = np.zeros((h, w), dtype=np.uint8)
            pts = np.array(poly, np.int32).reshape((-1, 1, 2))
            cv2.fillPoly(pm, [pts], 255)
            poly_masks.append(pm)

        # compute current overlaps (indices) using detections
        # store info per polygon: idx -> (class_id, conf, overlap_ratio, box)
        current_overlaps_info = {}
        for track_id, box, class_id, conf in zip(ids, boxes, class_ids, confs):
            # filter by confidence and allowed classes
            if conf < CONF_THRESHOLD:
                continue
            # optionally consider any class as obstacle
            if not ANY_CLASS_AS_OBSTACLE and len(allowed_class_ids) > 0 and (class_id not in allowed_class_ids):
                continue

            x1, y1, x2, y2 = box
            # clamp
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w - 1, x2), min(h - 1, y2)
            box_area = max(1, (x2 - x1) * (y2 - y1))
            if box_area < MIN_BOX_AREA:
                # ignore tiny detections
                continue

            # smooth box per track id: append to buffer and compute averaged box
            if track_id not in track_buffers:
                track_buffers[track_id] = deque(maxlen=BOX_SMOOTHING_FRAMES)
            track_buffers[track_id].append((x1, y1, x2, y2, conf))
            # compute averaged values
            bx1 = int(sum(b[0] for b in track_buffers[track_id]) / len(track_buffers[track_id]))
            by1 = int(sum(b[1] for b in track_buffers[track_id]) / len(track_buffers[track_id]))
            bx2 = int(sum(b[2] for b in track_buffers[track_id]) / len(track_buffers[track_id]))
            by2 = int(sum(b[3] for b in track_buffers[track_id]) / len(track_buffers[track_id]))
            bconf = float(sum(b[4] for b in track_buffers[track_id]) / len(track_buffers[track_id]))

            # use smoothed box (bx1,by1,bx2,by2) and smoothed conf for overlap
            x1, y1, x2, y2 = bx1, by1, bx2, by2
            box_area = max(1, (x2 - x1) * (y2 - y1))

            # create box mask
            bm = np.zeros((h, w), dtype=np.uint8)
            cv2.rectangle(bm, (x1, y1), (x2, y2), 255, -1)

            # check overlap with each polygon mask
            for idx, pm in enumerate(poly_masks):
                inter = cv2.bitwise_and(pm, bm)
                inter_area = cv2.countNonZero(inter)
                overlap_ratio = inter_area / float(box_area)
                if overlap_ratio >= OVERLAP_THRESHOLD:
                    # select best overlap/conf for this polygon
                    prev = current_overlaps_info.get(idx)
                    if (prev is None) or (overlap_ratio > prev[2]) or (conf > prev[1]):
                        current_overlaps_info[idx] = (int(class_id), float(conf), float(overlap_ratio), (x1, y1, x2, y2))
                    # highlight box for visibility (polygons will be drawn later)
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 0, 255), 2)
                    break

        # ensure occupancy_counters matches polygon count
        if len(occupancy_counters) < len(polygons):
            occupancy_counters.extend([0] * (len(polygons) - len(occupancy_counters)))
        elif len(occupancy_counters) > len(polygons):
            occupancy_counters = occupancy_counters[:len(polygons)]

        # update temporal counters: positive -> increment, negative -> decrement
        occupied_flags = [False] * len(polygons)
        for idx in range(len(polygons)):
            if idx in current_overlaps_info:
                occupancy_counters[idx] = min(occupancy_counters[idx] + 1, OCCUPANCY_CONFIRM_FRAMES)
            else:
                occupancy_counters[idx] = max(occupancy_counters[idx] - 1, -OCCUPANCY_RELEASE_FRAMES)
            # polygon considered occupied only after enough consecutive positives
            if occupancy_counters[idx] >= OCCUPANCY_CONFIRM_FRAMES:
                occupied_flags[idx] = True

        # count occupied zones
        occupied_zones = sum(1 for v in occupied_flags if v)

        # draw labels for occupied polygons showing detected class/conf
        for idx, occupied in enumerate(occupied_flags):
            if not occupied:
                continue
            info = current_overlaps_info.get(idx)
            # draw red polygon and label
            try:
                pts = np.array(polygons[idx], np.int32).reshape((-1, 1, 2))
                cv2.polylines(frame, [pts], isClosed=True, color=(0, 0, 255), thickness=2)
                if info is not None:
                    class_id, conf, overlap, box = info
                    # resolve class name
                    try:
                        cname = names[class_id] if isinstance(names, (list, tuple)) else names.get(class_id, str(class_id))
                    except Exception:
                        cname = str(class_id)
                    # (label removed per user request to avoid obstructing video)
            except Exception:
                pass

    total_zones = len(polygons)
    free_zones = total_zones - occupied_zones
    print("Free zones:", free_zones)
    # update shared status for email responder
    try:
        runtime_status.update(total_zones, free_zones, occupied_zones)
    except Exception:
        pass
    
    # Draw in-progress polygon points on video
    for pt in polygon_points:
        cv2.circle(frame, pt, 5, (0, 0, 255), -1)

    # Create separate info panel window
    info_panel = np.ones((600, 400, 3), dtype=np.uint8) * 40  # dark gray background
    
    # Title
    cv2.putText(info_panel, 'THONG TIN BAI DO XE', (50, 50), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    cv2.line(info_panel, (20, 70), (380, 70), (255, 255, 255), 2)
    
    # Statistics section
    y_pos = 120
    cv2.putText(info_panel, 'THONG KE:', (30, y_pos), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
    
    y_pos += 50
    cv2.putText(info_panel, f'Tong so khu: {total_zones}', (50, y_pos), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    
    y_pos += 40
    cv2.putText(info_panel, f'Khu trong: {free_zones}', (50, y_pos), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    
    y_pos += 40
    cv2.putText(info_panel, f'Da chiem: {occupied_zones}', (50, y_pos), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
    
    # Mode display
    y_pos += 60
    cv2.line(info_panel, (20, y_pos - 20), (380, y_pos - 20), (100, 100, 100), 1)
    cv2.putText(info_panel, 'CHE DO:', (30, y_pos), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
    
    y_pos += 40
    mode_text = 'Tat ca vatcan' if ANY_CLASS_AS_OBSTACLE else 'Chi phuong tien'
    mode_color = (0, 255, 0) if ANY_CLASS_AS_OBSTACLE else (255, 255, 0)
    cv2.putText(info_panel, mode_text, (50, y_pos), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, mode_color, 2)
    
    # Instructions section
    y_pos += 60
    cv2.line(info_panel, (20, y_pos - 20), (380, y_pos - 20), (100, 100, 100), 1)
    cv2.putText(info_panel, 'PHIM TAT:', (30, y_pos), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
    
    instructions = [
        ('SPACE', 'Play/Pause'),
        ('N', 'Khung tiep theo'),
        ('O', 'Doi che do'),
        ('R', 'Xoa khu cuoi'),
        ('C', 'Xoa tat ca'),
        ('ESC/Q', 'Thoat')
    ]
    
    y_pos += 35
    for key, desc in instructions:
        cv2.putText(info_panel, f'{key}:', (50, y_pos), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (150, 150, 150), 1)
        cv2.putText(info_panel, desc, (150, y_pos), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
        y_pos += 30
    
    # Show both windows
    cv2.imshow("RGB", frame)
    cv2.imshow("Thong Tin", info_panel)

    # Update trackbar position to current frame
    if use_trackbar:
        try:
            current = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
            cv2.setTrackbarPos('Frame', 'RGB', max(0, current - 1))
        except Exception:
            pass

    key = cv2.waitKey(1) & 0xFF
    if key == 27 or key == ord('q'):  # ESC or q to exit
        break
    elif key == ord(' '):  # space toggles play/pause
        playing = not playing
    elif key == ord('n'):  # next frame (when paused)
        # advance one frame
        playing = False
        cur = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
        cap.set(cv2.CAP_PROP_POS_FRAMES, cur + 1)
    elif key == ord('o'):
        # toggle any-class-as-obstacle mode
        ANY_CLASS_AS_OBSTACLE = not ANY_CLASS_AS_OBSTACLE
        print('ANY_CLASS_AS_OBSTACLE =', ANY_CLASS_AS_OBSTACLE)
    elif key == ord('r') and polygons:
        polygons.pop()
        # keep counters synced
        if occupancy_counters:
            occupancy_counters.pop()
        save_polygons()
    elif key == ord('c'):
        polygons.clear()
        occupancy_counters.clear()
        save_polygons()

cap.release()
cv2.destroyAllWindows()
# stop email responder thread
try:
    email_service.stop()
except Exception:
    pass
