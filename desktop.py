"""YRadar desktop search and live inventory monitor."""
import json
import os
from pathlib import Path
import queue
import sqlite3
import subprocess
import sys
import threading
import webbrowser
from urllib.parse import urlsplit

from buyer_search import BuyerSearch

ROOT = Path(__file__).resolve().parent
LOCAL = ROOT / '.yradar-local'
PROFILE = LOCAL / 'search.json'


def worker():
    from config import BUYER_SEARCH, STATE_DIR
    from monitor import run_loop
    if not BUYER_SEARCH:
        raise SystemExit('Open the desktop app to start a search.')
    Path(STATE_DIR).mkdir(parents=True, exist_ok=True)
    stopped = threading.Event()

    def listen():
        sys.stdin.readline()  # A stop request or a closed desktop window.
        stopped.set()

    threading.Thread(target=listen, daemon=True).start()
    run_loop(stopped)


def read_listings(search):
    db = LOCAL / search.state_key() / 'vehicles.db'
    if not db.is_file():
        return []
    conn = sqlite3.connect(db.as_uri() + '?mode=ro', uri=True, timeout=0.25)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(
            'SELECT * FROM vehicles WHERE is_active = 1 ORDER BY price')]
    finally:
        conn.close()


def app():
    import tkinter as tk
    from tkinter import ttk, messagebox
    from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
    from demo import DEMO_VEHICLES

    window = tk.Tk()
    window.title('YRadar | Inventory watch')
    window.geometry('1100x700')
    window.minsize(940, 650)
    style = ttk.Style(window)
    style.theme_use('clam')
    style.configure('.', font=('Segoe UI', 10), background='#f3f6fa', foreground='#20334b')
    style.configure('TNotebook', background='#f3f6fa', borderwidth=0)
    style.configure('TNotebook.Tab', padding=(12, 6))
    style.map('TNotebook.Tab', background=[('selected', '#ffffff')], foreground=[('selected', '#165e57')])
    style.configure('TButton', padding=(8, 5))
    style.configure('Treeview.Heading', background='#e8eef5', foreground='#283e59')
    style.map('Treeview', background=[('selected', '#d7ebe8')], foreground=[('selected', '#103c38')])
    style.configure('Treeview', rowheight=32, background='white', fieldbackground='white')
    style.configure('Treeview.Heading', font=('Segoe UI', 10, 'bold'))
    style.configure('Accent.TButton', foreground='white', background='#17665e', padding=(10, 8))
    style.map('Accent.TButton', background=[('active', '#0f514b'), ('disabled', '#617a78')])
    style.configure('Section.TLabel', font=('Segoe UI', 13, 'bold'))
    hdr = tk.Frame(window, bg='#122441', padx=20, pady=13)
    hdr.pack(fill='x')
    tk.Label(hdr, text='YRadar', bg='#122441', fg='#6be5cd',
             font=('Segoe UI', 23, 'bold')).pack(side='left')
    tk.Label(hdr, text='Official used inventory   /   Model Y + Model 3',
             bg='#122441', fg='white', padx=15).pack(side='left')
    phase = tk.StringVar(value='Offline preview')
    tk.Label(hdr, textvariable=phase, bg='#122441', fg='#bfece6').pack(side='right')

    body = ttk.Frame(window, padding=12)
    body.pack(fill='both', expand=True)
    body.columnconfigure(1, weight=1)
    body.rowconfigure(0, weight=1)
    left = ttk.Frame(body, padding=(0, 0, 12, 0))
    left.grid(row=0, column=0, sticky='nsew')
    ttk.Label(left, text='Your search', style='Section.TLabel').pack(anchor='w', pady=(0, 8))
    tabs = ttk.Notebook(left)
    tabs.pack(fill='x')
    filters, setup = (ttk.Frame(tabs, padding=10) for _ in range(2))
    tabs.add(filters, text='Criteria')
    tabs.add(setup, text='Location & alerts')

    try:
        saved = BuyerSearch(**json.loads(PROFILE.read_text(encoding='utf-8')))
    except (OSError, TypeError, ValueError):
        saved = BuyerSearch()
    form = {}
    def field(frame, key, label, choices=None, width=22):
        ttk.Label(frame, text=label).pack(anchor='w', pady=(4, 0))
        v = tk.StringVar(value=str(getattr(saved, key)))
        form[key] = v
        widget = (ttk.Combobox(frame, textvariable=v, values=choices, state='readonly', width=width)
                  if choices else ttk.Entry(frame, textvariable=v, width=width))
        widget.pack(fill='x', pady=(0, 2))

    def toggle(frame, key, label):
        form[key] = tk.BooleanVar(value=getattr(saved, key))
        ttk.Checkbutton(frame, text=label, variable=form[key]).pack(anchor='w')

    field(filters, 'model', 'Model', ('Both models', 'Model Y', 'Model 3'))
    field(filters, 'budget', 'Maximum price (USD)')
    field(filters, 'mileage', 'Maximum mileage (mi)')
    field(filters, 'year', 'Earliest model year')
    field(filters, 'interior', 'Interior', ('Any', 'Black', 'White'))
    toggle(filters, 'awd', 'Require all-wheel drive')
    toggle(filters, 'clean', 'Require reported clean history')
    toggle(filters, 'include_fee', 'Count transport toward budget')
    toggle(filters, 'allow_unknown', 'Include unquoted transport in total mode')
    for pair in [(('zip_code', 'Search ZIP'), ('region', 'US state')),
                 (('lat', 'Latitude (optional)'), ('lng', 'Longitude (optional)'))]:
        pair_row = ttk.Frame(setup)
        pair_row.pack(fill='x')
        for key, label in pair:
            cell = ttk.Frame(pair_row, padding=(0, 0, 4, 0))
            cell.pack(side='left', fill='x', expand=True)
            field(cell, key, label, width=12)
    field(setup, 'interval', 'Scan interval (minutes)')
    delivery = tk.StringVar(value='Telegram + window' if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID else 'Window only')
    ttk.Label(setup, text='Live alerts').pack(anchor='w', pady=(8, 0))
    ttk.Combobox(setup, textvariable=delivery, state='readonly',
                 values=('Window only', 'Telegram + window')).pack(fill='x')
    token, chat = tk.StringVar(value=TELEGRAM_TOKEN), tk.StringVar(value=TELEGRAM_CHAT_ID)
    for label, var in [('Bot token', token), ('Chat ID', chat)]:
        ttk.Label(setup, text=label).pack(anchor='w')
        ttk.Entry(setup, textvariable=var, show='*').pack(fill='x')

    right = ttk.Frame(body)
    right.grid(row=0, column=1, sticky='nsew')
    output_tabs = ttk.Notebook(right)
    output_tabs.pack(fill='both', expand=True)
    listings, activity = ttk.Frame(output_tabs, padding=10), ttk.Frame(output_tabs, padding=8)
    output_tabs.add(listings, text='Matches')
    output_tabs.add(activity, text='Live activity')
    summary = tk.StringVar()
    ttk.Label(listings, textvariable=summary, style='Section.TLabel').pack(anchor='w', pady=(2, 8))
    columns = ('Vehicle', 'Sticker', 'Transport', 'Mileage', 'HW4')
    table_frame = ttk.Frame(listings)
    table_frame.pack(fill='both', expand=True)
    table = ttk.Treeview(table_frame, columns=columns, show='headings', height=7, selectmode='browse')
    for key, width in zip(columns, (166, 88, 109, 90, 85)):
        table.heading(key, text=key)
        table.column(key, width=width, minwidth=66, stretch=True)
    scrollbar = ttk.Scrollbar(table_frame, command=table.yview)
    table.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side='right', fill='y')
    table.pack(fill='both', expand=True)
    details = tk.StringVar()
    ttk.Label(listings, text='Listing details', style='Section.TLabel').pack(anchor='w', pady=(12, 4))
    info = ttk.Label(listings, textvariable=details, justify='left', wraplength=595)
    info.pack(fill='x', anchor='w')
    info.bind('<Configure>', lambda event: info.configure(wraplength=max(160, event.width - 6)))
    log = tk.Text(activity, wrap='word', bg='#14243b', fg='#e2ecfa', state='disabled', width=1)
    log.pack(fill='both', expand=True)
    status = tk.StringVar(value='Preview uses fictional listings. Start monitoring for live alerts.')
    ttk.Label(window, textvariable=status, padding=(14, 5), anchor='w').pack(side='bottom', fill='x', before=body)
    events = queue.Queue()
    proc = None
    active = saved
    preview = True
    prep_process = None
    latest = tk.StringVar()
    ttk.Label(listings, textvariable=latest, foreground='#17665e').pack(anchor='w')
    closing = False
    rows = {}

    def criteria():
        kw = {key: value.get() for key, value in form.items()}
        for key in ('budget', 'mileage', 'year', 'interval'):
            try:
                kw[key] = int(kw[key])
            except ValueError:
                raise ValueError(f'Enter a whole number for {key}.') from None
        kw['region'] = kw['region'].strip().upper()
        return BuyerSearch(**kw)

    def selected(event=None):
        ids = table.selection()
        if not ids or ids[0] not in rows:
            details.set('Select a matching listing for configuration evidence.')
            link.configure(state='disabled')
            return
        v = rows[ids[0]]
        fee = v.get('transport_fee')
        total = 'pending quote' if fee is None else f"${v['price'] + fee:,.0f}"
        dist = v.get('distance_miles')
        if dist is None:
            dist = v.get('distance')
        miles = f'{dist:.0f} mi straight-line' if dist is not None else 'distance unknown'
        details.set('\n'.join([
            f"{v['year']} {v['model']}  ·  {v.get('trim') or 'Trim unknown'}",
            f"Sticker + transport: {total}  ·  {v.get('transport_fee_source') or 'unknown source'}",
            f"{v.get('dealer_address') or 'Location unknown'}  ·  {miles}",
            f"Interior: {v.get('color_int') or 'unknown'}  ·  Wheels: {v.get('wheel_inches') or '?'} in  ·  Seats: {v.get('seat_count') or '?'}",
            f"HW4: {v.get('hw4_status') or 'unknown'}  ({v.get('hw4_source') or 'unknown source'})  ·  {v.get('hw4_reason') or 'No supporting evidence'}",
            f"Matrix lights: {v.get('matrix_status') or 'unknown'}  ({v.get('matrix_source') or 'unknown source'})  ·  {v.get('matrix_reason') or 'No supporting evidence'}",
        ]))
        link.configure(state='disabled' if preview else 'normal')

    def open_listing():
        v = rows.get(next(iter(table.selection()), ''), {})
        url = v.get('url', '')
        dest = urlsplit(url)
        if not preview and dest.scheme == 'https' and dest.hostname == 'www.tesla.com':
            webbrowser.open(url)

    link = ttk.Button(listings, text='Open Tesla listing', command=open_listing)
    link.pack(anchor='w', pady=7)
    table.bind('<<TreeviewSelect>>', selected)

    def refresh():
        nonlocal rows
        try:
            source = DEMO_VEHICLES if preview else read_listings(active)
        except sqlite3.OperationalError as error:
            status.set(f'Waiting for inventory database: {error}')
            return
        previous = table.selection()
        rows = {v['vin']: v for v in source if active.match(v)}
        table.delete(*table.get_children())
        for key, v in sorted(rows.items(), key=lambda item: item[1]['price'] + (
                (item[1]['transport_fee'] if item[1].get('transport_fee') is not None else float('inf'))
                if active.include_fee else 0)):
            fee = v.get('transport_fee')
            fee_text = 'Unknown' if fee is None else f'${fee:,.0f}'
            if fee is not None and v.get('transport_fee_status') == 'estimated':
                fee_text = '~' + fee_text
            table.insert('', 'end', iid=key, values=(f"{v['year']} {v['model']}",
                         f"${v['price']:,.0f}", fee_text, f"{v['mileage']:,}", v.get('hw4_status') or 'unknown'))
        summary.set(f"{'Sample' if preview else 'Last observed'} matches   ·   {len(rows)}")
        if rows:
            table.selection_set(previous[0] if previous and previous[0] in rows else table.get_children()[0])
        selected()

    def demo():
        nonlocal active, preview
        try:
            active = criteria()
        except ValueError as error:
            return messagebox.showerror('Search settings', str(error))
        preview = True
        phase.set('Offline preview')
        status.set('Fictional inventory. Change your criteria and preview again.')
        output_tabs.select(listings)
        refresh()

    def start():
        nonlocal proc, active, preview
        if proc is not None or prep_process is not None:
            return
        try:
            search = criteria()
            use_telegram = delivery.get() == 'Telegram + window'
            if use_telegram and not (token.get().strip() and chat.get().strip()):
                raise ValueError('Enter your Telegram bot token and chat ID under Location & alerts.')
            LOCAL.mkdir(exist_ok=True)
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=LOCAL, delete=False) as f:
                f.write(search.encoded())
            os.replace(f.name, PROFILE)
            env = {**os.environ, **search.environment(), 'PYTHONIOENCODING': 'utf-8',
                   'TELEGRAM_TOKEN': token.get().strip() if use_telegram else '',
                   'TELEGRAM_CHAT_ID': chat.get().strip() if use_telegram else ''}
            proc = subprocess.Popen([sys.executable, '-u', str(ROOT/'desktop.py'), '--worker'],
                                    cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (ValueError, OSError) as error:
            return messagebox.showerror('Cannot start monitoring', str(error))
        active, preview = search, False
        def collect(child):
            with child.stdout:
                for line in child.stdout:
                    events.put(line.rstrip())
            events.put(child.wait())
        threading.Thread(target=collect, args=(proc,), daemon=True).start()
        set_running(True)
        phase.set('Monitoring')
        status.set('Monitoring started. Keep this window open to receive live alerts.')
        refresh()

    def prepare_browser():
        nonlocal prep_process
        if proc is not None or prep_process is not None:
            return
        try:
            env = {**os.environ, **criteria().environment()}
            prep_process = subprocess.Popen([sys.executable, str(ROOT/'warmup_tesla.py')], cwd=ROOT,
                                            env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (ValueError, OSError) as error:
            return messagebox.showerror('Browser setup', str(error))
        prep_btn.configure(state='disabled')
        start_btn.configure(state='disabled')
        status.set('Check inventory in the setup browser, then close that Chrome window.')

    def stop():
        if proc is not None and not proc.stdin.closed:
            try:
                proc.stdin.close()
            except OSError:
                pass
            phase.set('Stopping after current scan')
            stop_btn.configure(state='disabled')

    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from descendants(child)

    def set_running(running):
        for button in (start_btn, demo_btn, prep_btn):
            button.configure(state='disabled' if running else 'normal')
        stop_btn.configure(state='normal' if running else 'disabled')
        for tab in (filters, setup):
            for widget in descendants(tab):
                if isinstance(widget, (ttk.Combobox, ttk.Entry, ttk.Checkbutton)):
                    widget.state(['disabled'] if running else ['!disabled'])

    def poll():
        nonlocal proc, prep_process
        if prep_process is not None and prep_process.poll() is not None:
            status.set('Browser setup finished.' if prep_process.returncode == 0 else 'Browser setup failed. Check your Chrome installation.')
            prep_process = None
            set_running(False)
        for _ in range(160):
            try:
                item = events.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, int):
                if proc and not proc.stdin.closed:
                    proc.stdin.close()
                proc = None
                if closing:
                    window.destroy()
                    return
                set_running(False)
                phase.set('Stopped' if not item else 'Monitor exited')
                status.set('Monitoring stopped.' if not item else 'Monitor exited. Check Live activity.')
                refresh()
                continue
            log.configure(state='normal')
            log.insert('end', item + '\n')
            if int(log.index('end-1c').split('.')[0]) > 500:
                log.delete('1.0', '51.0')
            log.see('end')
            log.configure(state='disabled')
            if '[notify]' in item:
                latest.set('Latest alert  ·  ' + item.partition('[notify]')[2].strip())
            if '[notify]' in item or ' done in ' in item or '[alert]' in item:
                status.set(item[:145])
            if ' done in ' in item:
                refresh()
        window.after(250, poll)

    def close():
        nonlocal closing
        if prep_process is not None:
            messagebox.showinfo('Browser setup', 'Close the setup Chrome window before exiting.')
        elif proc is None:
            window.destroy()
        elif messagebox.askyesno('Stop monitoring?', 'Finish the current scan and close YRadar?'):
            closing = True
            stop()

    prep_btn = ttk.Button(setup, text='Open setup browser', command=prepare_browser)
    prep_btn.pack(fill='x', pady=(6, 0))
    demo_btn = ttk.Button(left, text='Preview with sample cars', command=demo)
    demo_btn.pack(fill='x', pady=(10, 3))
    start_btn = ttk.Button(left, text='Start live monitoring', command=start, style='Accent.TButton')
    start_btn.pack(fill='x', pady=2)
    stop_btn = ttk.Button(left, text='Stop monitoring', command=stop, state='disabled')
    stop_btn.pack(fill='x', pady=2)
    window.protocol('WM_DELETE_WINDOW', close)
    refresh()
    poll()
    return window


if __name__ == '__main__':
    if sys.argv[1:] == ['--worker']:
        worker()
    else:
        app().mainloop()
