import cv2
import json
import os
import numpy as np
from collections import deque
from ultralytics import YOLO
import runtime_status
import email_service
import gui

# ===== Helper: get screen size to adapt Manager UI =====
def get_screen_size():
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        w = root.winfo_screenwidth()
        h = root.winfo_screenheight()
        root.destroy()
        return w, h
    except Exception:
        return 1280, 720

# ===== Load UTH logo (PNG/JPG) neu co =====
UTH_LOGO_PATH = 'uth_logo.png'
uth_logo = None
if os.path.exists(UTH_LOGO_PATH):
    tmp = cv2.imread(UTH_LOGO_PATH)
    if tmp is not None:
        uth_logo = tmp

# Load YOLOv8 model
model = YOLO('best.pt')
names = model.names

cap = cv2.VideoCapture("demonay.mp4")
frame_count = 0

# Detection config
CONF_THRESHOLD = 0.35
OVERLAP_THRESHOLD = 0.2
SKIP_FRAMES = 0

# Temporal smoothing for occupancy
OCCUPANCY_CONFIRM_FRAMES = 3
OCCUPANCY_RELEASE_FRAMES = 2

# Box smoothing per track id
BOX_SMOOTHING_FRAMES = 3
track_buffers = {}  # track_id -> deque of (x1,y1,x2,y2,conf)

# Obstacle detection settings
ANY_CLASS_AS_OBSTACLE = False
MIN_BOX_AREA = 300

# allowed object classes to consider as vehicles
VEHICLE_CLASS_NAMES = {'car', 'truck', 'bus', 'motorcycle', 'bicycle'}
allowed_class_ids = [
    i for i, n in (names.items() if isinstance(names, dict) else enumerate(names))
    if (n in VEHICLE_CLASS_NAMES)
]

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
        print("Warning: polygons.json invalid, resetting.")
        polygons = []
        with open(polygon_file, 'w') as f:
            json.dump(polygons, f)

# Save polygons to JSON
def save_polygons():
    with open(polygon_file, 'w') as f:
        json.dump(polygons, f)

# occupancy counters per polygon (temporal smoothing)
occupancy_counters = []
if polygons:
    occupancy_counters = [0] * len(polygons)

# ====== INFO PANEL BUTTON STATE ======
info_buttons = []          # list cac nut tren cua so Thong Tin
info_click_action = None   # action duoc click

# Mouse callback to add polygon points tren khung RGB
def RGB(event, x, y, flags, param):
    global polygon_points, polygons, occupancy_counters
    if event == cv2.EVENT_LBUTTONDOWN:
        polygon_points.append((x, y))
        if len(polygon_points) == 4:
            polygons.append(polygon_points.copy())
            occupancy_counters.append(0)
            save_polygons()
            polygon_points.clear()

# Mouse callback tren cua so "Thong Tin"
def on_info_mouse(event, x, y, flags, param):
    global info_click_action, info_buttons
    if event == cv2.EVENT_LBUTTONDOWN:
        for btn in info_buttons:
            x1, y1, x2, y2 = btn["rect"]
            if x1 <= x <= x2 and y1 <= y <= y2:
                info_click_action = btn["action"]
                break

cv2.namedWindow("RGB")
cv2.setMouseCallback("RGB", RGB)
cv2.namedWindow("Manager", cv2.WINDOW_NORMAL)
cv2.namedWindow("Thong Tin", cv2.WINDOW_NORMAL)
cv2.setMouseCallback("Thong Tin", on_info_mouse)

# Video/frame selection controls
total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
use_trackbar = total_frames > 0
if use_trackbar:
    def on_trackbar(pos):
        cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
    cv2.createTrackbar('Frame', 'RGB', 0, max(0, total_frames - 1), on_trackbar)

# Playback control
playing = True
last_trackbar_pos = -1

# start email responder thread if enabled
email_service.start_if_enabled()

# start GUI in background
try:
    gui.start()
except Exception:
    pass
try:
    gui.start_compact()
except Exception:
    pass

# Determine base manager size from screen (responsive)
screen_w, screen_h = get_screen_size()
BASE_W, BASE_H = 360, 680
scale0 = min(screen_w / 480.0, screen_h / 800.0)
scale0 = max(0.8, min(scale0, 1.6))  # gioi han zoom
mgr_w = int(BASE_W * scale0)
mgr_h = int(BASE_H * scale0)
ui_scale = mgr_h / float(BASE_H)

# ===== Helper ve button tren info_panel =====
def draw_info_button(panel, x1, y1, x2, y2, text, action):
    """Ve nut don gian + luu lai toa do de click chuot."""
    global info_buttons
    cv2.rectangle(panel, (x1, y1), (x2, y2), (70, 70, 70), -1)
    cv2.rectangle(panel, (x1, y1), (x2, y2), (130, 130, 130), 1)
    # can giua text
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
    tx = x1 + (x2 - x1 - tw) // 2
    ty = y1 + (y2 - y1 + th) // 2
    cv2.putText(panel, text, (tx, ty),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (230, 230, 230), 1)
    info_buttons.append({"rect": (x1, y1, x2, y2), "action": action})

while True:
    # follow trackbar when user seek
    if use_trackbar:
        try:
            pos = cv2.getTrackbarPos('Frame', 'RGB')
            if pos != last_trackbar_pos:
                cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
                last_trackbar_pos = pos
        except Exception:
            use_trackbar = False

    ret, frame = cap.read()
    if not ret:
        break

    frame_count += 1
    if SKIP_FRAMES > 0 and (frame_count % (SKIP_FRAMES + 1) != 0) and playing:
        continue

    frame = cv2.resize(frame, (1020, 500))
    results = model.track(frame, persist=True)

    # Draw saved polygons
    for poly in polygons:
        pts = np.array(poly, np.int32).reshape((-1, 1, 2))
        cv2.polylines(frame, [pts], isClosed=True, color=(0, 255, 0), thickness=2)

    # occupancy logic
    occupied_zones = 0
    if results[0].boxes.id is not None:
        ids = results[0].boxes.id.cpu().numpy().astype(int)
        boxes = results[0].boxes.xyxy.cpu().numpy().astype(int)
        class_ids = results[0].boxes.cls.int().cpu().numpy().astype(int)
        confs = results[0].boxes.conf.cpu().numpy()

        poly_masks = []
        h, w = frame.shape[:2]
        for poly in polygons:
            pm = np.zeros((h, w), dtype=np.uint8)
            pts = np.array(poly, np.int32).reshape((-1, 1, 2))
            cv2.fillPoly(pm, [pts], 255)
            poly_masks.append(pm)

        current_overlaps_info = {}
        for track_id, box, class_id, conf in zip(ids, boxes, class_ids, confs):
            if conf < CONF_THRESHOLD:
                continue
            if (not ANY_CLASS_AS_OBSTACLE and
                    len(allowed_class_ids) > 0 and
                    class_id not in allowed_class_ids):
                continue

            x1, y1, x2, y2 = box
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w - 1, x2), min(h - 1, y2)
            box_area = max(1, (x2 - x1) * (y2 - y1))
            if box_area < MIN_BOX_AREA:
                continue

            # smooth box per track id
            if track_id not in track_buffers:
                track_buffers[track_id] = deque(maxlen=BOX_SMOOTHING_FRAMES)
            track_buffers[track_id].append((x1, y1, x2, y2, conf))

            bx1 = int(sum(b[0] for b in track_buffers[track_id]) / len(track_buffers[track_id]))
            by1 = int(sum(b[1] for b in track_buffers[track_id]) / len(track_buffers[track_id]))
            bx2 = int(sum(b[2] for b in track_buffers[track_id]) / len(track_buffers[track_id]))
            by2 = int(sum(b[3] for b in track_buffers[track_id]) / len(track_buffers[track_id]))
            bconf = float(sum(b[4] for b in track_buffers[track_id]) / len(track_buffers[track_id]))

            x1, y1, x2, y2 = bx1, by1, bx2, by2
            box_area = max(1, (x2 - x1) * (y2 - y1))

            bm = np.zeros((h, w), dtype=np.uint8)
            cv2.rectangle(bm, (x1, y1), (x2, y2), 255, -1)

            for idx, pm in enumerate(poly_masks):
                inter = cv2.bitwise_and(pm, bm)
                inter_area = cv2.countNonZero(inter)
                overlap_ratio = inter_area / float(box_area)
                if overlap_ratio >= OVERLAP_THRESHOLD:
                    prev = current_overlaps_info.get(idx)
                    if (prev is None) or (overlap_ratio > prev[2]) or (conf > prev[1]):
                        current_overlaps_info[idx] = (
                            int(class_id),
                            float(conf),
                            float(overlap_ratio),
                            (x1, y1, x2, y2),
                        )
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 0, 255), 2)
                    break

        # sync counters
        if len(occupancy_counters) < len(polygons):
            occupancy_counters.extend([0] * (len(polygons) - len(occupancy_counters)))
        elif len(occupancy_counters) > len(polygons):
            occupancy_counters = occupancy_counters[:len(polygons)]

        occupied_flags = [False] * len(polygons)
        for idx in range(len(polygons)):
            if idx in current_overlaps_info:
                occupancy_counters[idx] = min(
                    occupancy_counters[idx] + 1, OCCUPANCY_CONFIRM_FRAMES
                )
            else:
                occupancy_counters[idx] = max(
                    occupancy_counters[idx] - 1, -OCCUPANCY_RELEASE_FRAMES
                )

            if occupancy_counters[idx] >= OCCUPANCY_CONFIRM_FRAMES:
                occupied_flags[idx] = True

        occupied_zones = sum(1 for v in occupied_flags if v)

        for idx, occupied in enumerate(occupied_flags):
            if not occupied:
                continue
            try:
                pts = np.array(polygons[idx], np.int32).reshape((-1, 1, 2))
                cv2.polylines(frame, [pts], isClosed=True, color=(0, 0, 255), thickness=2)
            except Exception:
                pass

    total_zones = len(polygons)
    free_zones = total_zones - occupied_zones

    # update shared status + preview bytes
    try:
        runtime_status.update(total_zones, free_zones, occupied_zones)
        ret2, buf = cv2.imencode('.jpg', frame)
        if ret2:
            runtime_status.set_frame_bytes(buf.tobytes())
    except Exception:
        pass

    # =========================
    # UTH PARKING - Manager UI (responsive)
    # =========================
    try:
        mgr = np.zeros((mgr_h, mgr_w, 3), dtype=np.uint8)

        # gradient background
        bg_top = (245, 245, 245)
        bg_bottom = (170, 220, 200)
        for i in range(mgr_h):
            a = i / max(1, mgr_h - 1)
            b = int(bg_top[0] * (1 - a) + bg_bottom[0] * a)
            g = int(bg_top[1] * (1 - a) + bg_bottom[1] * a)
            r = int(bg_top[2] * (1 - a) + bg_bottom[2] * a)
            mgr[i, :] = (b, g, r)

        # layout metrics
        margin_x = int(0.04 * mgr_w)
        header_top = int(0.02 * mgr_h)
        header_bottom = int(0.17 * mgr_h)
        card_top = int(0.19 * mgr_h)
        card_bottom = int(0.50 * mgr_h)
        live_top = int(0.52 * mgr_h)
        live_bottom = int(0.68 * mgr_h)
        stats_top = int(0.70 * mgr_h)
        progress_top = int(0.84 * mgr_h)
        footer_y = int(0.93 * mgr_h)

        # HEADER
        cv2.rectangle(mgr, (margin_x, header_top),
                      (mgr_w - margin_x, header_bottom),
                      (255, 255, 255), -1, cv2.LINE_AA)
        cv2.rectangle(mgr, (margin_x, header_top),
                      (mgr_w - margin_x, header_bottom),
                      (210, 210, 210), 1, cv2.LINE_AA)

        header_height = header_bottom - header_top
        logo_h_target = int(header_height * 0.7)
        header_center_y = header_top + header_height // 2

        if uth_logo is not None:
            h_l, w_l = uth_logo.shape[:2]
            scale_logo = logo_h_target / float(h_l)
            new_w = int(w_l * scale_logo)
            logo_resized = cv2.resize(uth_logo, (new_w, logo_h_target),
                                      interpolation=cv2.INTER_AREA)
            logo_x = margin_x + int(0.03 * mgr_w)
            logo_y = header_center_y - logo_h_target // 2
            roi = mgr[logo_y:logo_y + logo_h_target, logo_x:logo_x + new_w]
            if roi.shape[:2] == logo_resized.shape[:2]:
                mgr[logo_y:logo_y + logo_h_target,
                    logo_x:logo_x + new_w] = logo_resized
            text_x = logo_x + new_w + int(0.03 * mgr_w)
        else:
            text_x = margin_x + int(0.25 * mgr_w)

        fs_title = 0.85 * ui_scale
        fs_sub = 0.55 * ui_scale
        cv2.putText(mgr, 'UTH PARKING', (text_x, header_top + int(0.55 * header_height)),
                    cv2.FONT_HERSHEY_DUPLEX, fs_title,
                    (20, 120, 110), int(2 * ui_scale), cv2.LINE_AA)
        cv2.putText(mgr, 'Smart Parking System',
                    (text_x, header_top + int(0.85 * header_height)),
                    cv2.FONT_HERSHEY_SIMPLEX, fs_sub,
                    (80, 130, 130), max(1, int(1 * ui_scale)), cv2.LINE_AA)

        # Card "CHO TRONG"
        overlay = mgr.copy()
        cv2.rectangle(overlay, (margin_x, card_top),
                      (mgr_w - margin_x, card_bottom),
                      (255, 255, 255), -1, cv2.LINE_AA)
        mgr = cv2.addWeighted(overlay, 0.9, mgr, 0.1, 0)
        cv2.rectangle(mgr, (margin_x, card_top),
                      (mgr_w - margin_x, card_bottom),
                      (210, 230, 225), 1, cv2.LINE_AA)

        cv2.putText(mgr, 'CHO TRONG',
                    (margin_x + int(0.05 * mgr_w),
                     card_top + int(0.13 * (card_bottom - card_top))),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7 * ui_scale,
                    (60, 140, 130), max(1, int(2 * ui_scale)), cv2.LINE_AA)

        circle_cx = mgr_w // 2
        circle_cy = card_top + int(0.55 * (card_bottom - card_top))
        radius = int(0.30 * (card_bottom - card_top))
        cv2.circle(mgr, (circle_cx, circle_cy),
                   radius + int(4 * ui_scale), (210, 235, 230),
                   max(1, int(2 * ui_scale)), cv2.LINE_AA)
        cv2.circle(mgr, (circle_cx, circle_cy),
                   radius, (235, 255, 250), -1, cv2.LINE_AA)

        free_text = str(free_zones)
        font_num = cv2.FONT_HERSHEY_DUPLEX
        scale_num = 3.0 * ui_scale
        thick_num = max(1, int(5 * ui_scale))
        (tw, th), _ = cv2.getTextSize(free_text, font_num, scale_num, thick_num)
        fx = circle_cx - tw // 2
        fy = circle_cy + th // 3

        cv2.putText(mgr, free_text, (fx + 2, fy + 2),
                    font_num, scale_num, (200, 200, 200),
                    thick_num, cv2.LINE_AA)
        num_color = (80, 180, 150) if free_zones > 0 else (40, 80, 200)
        cv2.putText(mgr, free_text, (fx, fy),
                    font_num, scale_num, num_color,
                    thick_num, cv2.LINE_AA)

        status_text = 'CON CHO' if free_zones > 0 else 'FULL'
        status_color = (60, 150, 130) if free_zones > 0 else (0, 0, 200)
        (stw, sth), _ = cv2.getTextSize(status_text,
                                        cv2.FONT_HERSHEY_SIMPLEX,
                                        0.85 * ui_scale,
                                        max(1, int(2 * ui_scale)))
        cv2.putText(
            mgr,
            status_text,
            (circle_cx - stw // 2, card_bottom - int(0.08 * (card_bottom - card_top))),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.85 * ui_scale,
            status_color,
            max(1, int(2 * ui_scale)),
            cv2.LINE_AA,
        )

        # LIVE preview video
        overlay = mgr.copy()
        cv2.rectangle(overlay, (margin_x, live_top),
                      (mgr_w - margin_x, live_bottom),
                      (255, 255, 255), -1, cv2.LINE_AA)
        mgr = cv2.addWeighted(overlay, 0.9, mgr, 0.1, 0)
        cv2.rectangle(mgr, (margin_x, live_top),
                      (mgr_w - margin_x, live_bottom),
                      (210, 230, 225), 1, cv2.LINE_AA)

        cv2.putText(mgr, 'LIVE VIEW',
                    (margin_x + int(0.02 * mgr_w),
                     live_top + int(0.12 * (live_bottom - live_top))),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6 * ui_scale,
                    (120, 150, 145), max(1, int(1 * ui_scale)), cv2.LINE_AA)

        try:
            preview_src = frame.copy()
            view_w = (mgr_w - margin_x) - (margin_x + int(0.02 * mgr_w))
            view_h = int(0.6 * (live_bottom - live_top))
            preview = cv2.resize(preview_src, (view_w, view_h))
            roi_y1 = live_top + int(0.25 * (live_bottom - live_top))
            roi_y2 = roi_y1 + view_h
            roi_x1 = margin_x + int(0.02 * mgr_w)
            roi_x2 = roi_x1 + view_w
            mgr[roi_y1:roi_y2, roi_x1:roi_x2] = preview
        except Exception:
            pass

        # Stats boxes
        box_area_height = int(0.11 * mgr_h)
        box_top = stats_top
        box_bottom = box_top + box_area_height
        box_gap = int(0.03 * mgr_w)
        box_w = int((mgr_w - 2 * margin_x - 2 * box_gap) / 3)

        labels = ['Tong', 'Chiem', 'Trong']
        values = [total_zones, occupied_zones, free_zones]
        colors = [
            (60, 140, 130),
            (80, 110, 200),
            (60, 160, 120),
        ]

        for i in range(3):
            x1 = margin_x + i * (box_w + box_gap)
            x2 = x1 + box_w
            overlay = mgr.copy()
            cv2.rectangle(overlay, (x1, box_top), (x2, box_bottom),
                          (255, 255, 255), -1, cv2.LINE_AA)
            mgr = cv2.addWeighted(overlay, 0.9, mgr, 0.1, 0)
            cv2.rectangle(mgr, (x1, box_top), (x2, box_bottom),
                          (220, 235, 230), 1, cv2.LINE_AA)

            cv2.putText(mgr, labels[i], (x1 + int(0.08 * box_w), box_top + int(0.35 * box_area_height)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6 * ui_scale,
                        (140, 140, 140), max(1, int(2 * ui_scale)), cv2.LINE_AA)

            v_text = str(values[i])
            (vw, vh), _ = cv2.getTextSize(v_text,
                                          cv2.FONT_HERSHEY_DUPLEX,
                                          0.95 * ui_scale,
                                          max(1, int(2 * ui_scale)))
            cv2.putText(
                mgr,
                v_text,
                (x1 + (box_w - vw) // 2, box_top + int(0.8 * box_area_height)),
                cv2.FONT_HERSHEY_DUPLEX,
                0.95 * ui_scale,
                colors[i],
                max(1, int(2 * ui_scale)),
                cv2.LINE_AA,
            )

        # Progress bar
        pct = float(occupied_zones) / float(total_zones) if total_zones > 0 else 0.0
        pct = max(0.0, min(1.0, pct))

        bar_x1 = margin_x
        bar_x2 = mgr_w - margin_x
        bar_y = progress_top
        bar_h = int(0.035 * mgr_h)

        cv2.putText(mgr, 'Muc do su dung', (bar_x1, bar_y - int(0.2 * bar_h)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55 * ui_scale,
                    (70, 120, 115), max(1, int(1 * ui_scale)), cv2.LINE_AA)

        cv2.rectangle(mgr, (bar_x1, bar_y),
                      (bar_x2, bar_y + bar_h),
                      (220, 235, 230), -1, cv2.LINE_AA)
        cv2.rectangle(mgr, (bar_x1, bar_y),
                      (bar_x2, bar_y + bar_h),
                      (190, 210, 205), 1, cv2.LINE_AA)

        fill_w = int((bar_x2 - bar_x1) * pct)
        if fill_w > 2:
            if pct < 0.5:
                bar_color = (80, 170, 140)
            elif pct < 0.8:
                bar_color = (60, 150, 220)
            else:
                bar_color = (0, 80, 200)
            cv2.rectangle(mgr, (bar_x1 + 1, bar_y + 1),
                          (bar_x1 + fill_w - 2, bar_y + bar_h - 1),
                          bar_color, -1, cv2.LINE_AA)

        # Footer
        cv2.putText(mgr, 'UTH PARKING  v1.0',
                    (margin_x, footer_y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6 * ui_scale,
                    (60, 110, 105), max(1, int(1 * ui_scale)), cv2.LINE_AA)

        cv2.imshow('Manager', mgr)
    except Exception:
        pass

    # draw polygon points tren frame chinh
    for pt in polygon_points:
        cv2.circle(frame, pt, 5, (0, 0, 255), -1)

    # ====== INFO PANEL (BANG THONG TIN + NUT CHINH SUA) ======
    panel_w = 420
    panel_h = 260
    info_panel = np.zeros((panel_h, panel_w, 3), dtype=np.uint8)
    info_panel[:] = (30, 30, 30)  # nen xam dam

    fs = 0.6
    th = 1
    dy = 35
    y = 40

    cv2.putText(info_panel, "THONG TIN HE THONG", (20, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 255, 200), 2)

    cv2.putText(info_panel, f"Tong o: {total_zones}", (20, y),
                cv2.FONT_HERSHEY_SIMPLEX, fs, (220, 220, 220), th); y += dy

    cv2.putText(info_panel, f"Dang chiem: {occupied_zones}", (20, y),
                cv2.FONT_HERSHEY_SIMPLEX, fs, (80, 170, 250), th); y += dy

    cv2.putText(info_panel, f"Con trong: {free_zones}", (20, y),
                cv2.FONT_HERSHEY_SIMPLEX, fs, (100, 250, 180), th); y += dy

    pct2 = (occupied_zones / total_zones * 100) if total_zones > 0 else 0
    cv2.putText(info_panel, f"Muc do su dung: {pct2:.1f}%", (20, y),
                cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 210, 120), th); y += dy

    mode_text = "Moi vat la vat can" if ANY_CLASS_AS_OBSTACLE else "Chi xe / xe may"
    cv2.putText(info_panel, f"Che do phat hien: {mode_text}", (20, y),
                cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 200, 200), th)

    # ----- Vung nut bam o duoi -----
    info_buttons = []  # reset danh sach nut moi frame
    btn_y1 = panel_h - 60
    btn_y2 = panel_h - 25
    btn_w = 110
    gap = 15
    start_x = 20

    # Nut 1: Xoa cuoi
    draw_info_button(info_panel,
                     start_x,
                     btn_y1,
                     start_x + btn_w,
                     btn_y2,
                     "Xoa cuoi",
                     "del_last")

    # Nut 2: Xoa tat ca
    draw_info_button(info_panel,
                     start_x + (btn_w + gap),
                     btn_y1,
                     start_x + (btn_w + gap) + btn_w,
                     btn_y2,
                     "Xoa tat ca",
                     "del_all")

    # Nut 3: Che do (toggle ANY_CLASS_AS_OBSTACLE)
    text_mode_btn = "Che do moi vat" if not ANY_CLASS_AS_OBSTACLE else "Che do xe"
    draw_info_button(info_panel,
                     start_x + 2 * (btn_w + gap),
                     btn_y1,
                     start_x + 2 * (btn_w + gap) + btn_w,
                     btn_y2,
                     text_mode_btn,
                     "toggle_mode")

    cv2.imshow("Thong Tin", info_panel)
    cv2.imshow("RGB", frame)

    # Xu ly action click tu cua so Thong Tin
    if info_click_action is not None:
        if info_click_action == "del_last" and polygons:
            polygons.pop()
            if occupancy_counters:
                occupancy_counters.pop()
            save_polygons()
        elif info_click_action == "del_all":
            polygons.clear()
            occupancy_counters.clear()
            save_polygons()
        elif info_click_action == "toggle_mode":
            ANY_CLASS_AS_OBSTACLE = not ANY_CLASS_AS_OBSTACLE

        info_click_action = None  # reset

    # cap nhat trackbar theo frame hien tai
    if use_trackbar:
        try:
            current = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
            cv2.setTrackbarPos('Frame', 'RGB', max(0, current - 1))
        except Exception:
            pass

    # hotkeys
    key = cv2.waitKey(1) & 0xFF
    if key in (27, ord('q')):  # ESC hoac q
        break
    elif key == ord(' '):      # space: play/pause (neu sau muon lam)
        playing = not playing
    elif key == ord('n'):      # next frame
        playing = False
        cur = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
        cap.set(cv2.CAP_PROP_POS_FRAMES, cur + 1)
    elif key == ord('o'):      # toggle obstacle mode (tu ban phim)
        ANY_CLASS_AS_OBSTACLE = not ANY_CLASS_AS_OBSTACLE
    elif key == ord('r') and polygons:
        polygons.pop()
        if occupancy_counters:
            occupancy_counters.pop()
        save_polygons()
    elif key == ord('c'):
        polygons.clear()
        occupancy_counters.clear()
        save_polygons()

cap.release()
cv2.destroyAllWindows()
