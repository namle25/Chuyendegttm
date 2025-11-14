
import threading
import runtime_status

_thread = None
_thread_compact = None

try:
    import PySimpleGUI as sg
    _HAS_SG = True
except Exception:
    sg = None
    _HAS_SG = False


def _run_gui():
    if not _HAS_SG:
        return
    # Modern themed UI with large vacancy number and preview
    sg.theme('SystemDefaultForReal')

    # Left: visual card with counts; Right: live preview image
    left_col = [
        [sg.Text('BÃI GIỮ XE', font=('Helvetica', 12, 'bold'), justification='center', expand_x=True)],
        [sg.Text('CÒN TRỐNG', font=('Helvetica', 10), text_color='#888888')],
        [sg.Text('0', key='-FREE-BIG-', font=('Helvetica', 72, 'bold'), text_color='#00B14F')],
        [sg.Column([
            [sg.Text('Tổng: ', font=('Helvetica', 10)), sg.Text('0', key='-TOTAL-', font=('Helvetica', 10, 'bold'))],
            [sg.Text('Đã chiếm: ', font=('Helvetica', 10)), sg.Text('0', key='-OCC-', font=('Helvetica', 10, 'bold'))],
        ], element_justification='left')],
        [sg.Button('Làm mới', key='-REFRESH-', size=(10,1)), sg.Button('Đóng', key='-CLOSE-', size=(10,1))]
    ]

    right_col = [
        [sg.Text('Xem trước', font=('Helvetica', 10, 'bold'))],
        [sg.Image(key='-IMAGE-', size=(640,360))],
    ]

    layout = [[sg.Column(left_col, vertical_alignment='center', element_justification='center', pad=(12,12), background_color='#F6F8FA'),
               sg.VerticalSeparator(),
               sg.Column(right_col, pad=(12,12))]]

    window = sg.Window('Parking Dashboard', layout, finalize=True, resizable=True, size=(980,520))

    try:
        while True:
            event, values = window.read(timeout=200)
            if event in (sg.WIN_CLOSED, '-CLOSE-'):
                break
            if event == '-REFRESH-':
                # immediate refresh (values will be updated in loop anyway)
                pass

            st = runtime_status.get_status()
            total = int(st.get('total', 0))
            free = int(st.get('free', 0))
            occ = int(st.get('occupied', 0))

            # update counts
            window['-TOTAL-'].update(str(total))
            window['-FREE-BIG-'].update(str(free))
            window['-OCC-'].update(str(occ))

            # color the big number depending on availability
            try:
                if free > 0:
                    window['-FREE-BIG-'].update(text_color='#00B14F')
                else:
                    window['-FREE-BIG-'].update(text_color='#D32F2F')
            except Exception:
                pass

            img = runtime_status.get_frame_bytes()
            if img:
                try:
                    window['-IMAGE-'].update(data=img)
                except Exception:
                    pass
    finally:
        window.close()


def start():
    global _thread
    if not _HAS_SG:
        print('[GUI] PySimpleGUI not installed; skipping GUI.')
        return
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_run_gui, daemon=True)
    _thread.start()


def _run_compact():
    """Small always-on-top window that shows only the free count in large font."""
    if not _HAS_SG:
        return
    sg.theme('DarkBlue')
    layout = [[sg.Text('CÒN TRỐNG', font=('Helvetica', 10), justification='center')],
              [sg.Text('0', key='-COMPACT-FREE-', font=('Helvetica', 48, 'bold'), text_color='#00B14F', justification='center')]]

    # make a compact window always on top
    window = sg.Window('Vacancy', layout, finalize=True, keep_on_top=True, no_titlebar=False, element_justification='center', resizable=False)

    try:
        while True:
            event, values = window.read(timeout=300)
            if event == sg.WIN_CLOSED:
                break
            st = runtime_status.get_status()
            free = int(st.get('free', 0))
            try:
                window['-COMPACT-FREE-'].update(str(free))
                if free > 0:
                    window['-COMPACT-FREE-'].update(text_color='#00B14F')
                else:
                    window['-COMPACT-FREE-'].update(text_color='#D32F2F')
            except Exception:
                pass
    finally:
        window.close()


def start_compact():
    global _thread_compact
    if not _HAS_SG:
        print('[GUI] PySimpleGUI not installed; skipping compact window.')
        return
    if _thread_compact and _thread_compact.is_alive():
        return
    _thread_compact = threading.Thread(target=_run_compact, daemon=True)
    _thread_compact.start()
