import configparser
import csv
import os
import sqlite3
import threading
import tkinter as tk
import tkinter.font as tkfont
import requests
from tenacity import retry, stop_after_attempt, wait_fixed

from tkinter import filedialog, messagebox, ttk

verzija = "0.0.1"

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def call_perplexity(system_msg: str, content: str, api_key: str = None, model: str = None) -> str:
    if not api_key or not model:
        s = load_settings()
        api_key = api_key or s.get('api_key')
        model   = model   or s.get('model')

    if not api_key:
        return "[Napaka: Perplexity API ključ ni nastavljen.]"

    url = "https://api.perplexity.ai/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user",   "content": content}
        ],
        "include_citations": False        
    }

    try:
        resp = requests.post(url, headers=headers, json=payload,timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"[Napaka pri klicu Perplexity API: {e}]"

SETTINGS_FILE = os.path.join(os.getcwd(), 'settings.ini')

def load_settings():
    config = configparser.ConfigParser()
    defaults = {
        'api_key': '', 'model': '',
        'system_msg': '',
        'last_db': '', 'last_row': None, 'col_widths': {},
        'win_width': 850, 'win_height': 650
    }
    if os.path.exists(SETTINGS_FILE):
        config.read(SETTINGS_FILE)
        if 'Perplexity' in config:
            defaults['api_key'] = config['Perplexity'].get('api_key', defaults['api_key'])
            defaults['model'] = config['Perplexity'].get('model', defaults['model'])
        if 'User' in config:
            u = config['User']
            defaults['system_msg'] = u.get('system_msg', defaults['system_msg'])
            defaults['last_db'] = u.get('last_db', defaults['last_db'])
            lr = u.get('last_row','')
            defaults['last_row'] = int(lr) if lr.isdigit() else None
            cw = {}
            for pair in u.get('col_widths','').split(','):
                if ':' in pair:
                    c,w = pair.split(':',1)
                    if w.isdigit(): cw[c] = int(w)
            defaults['col_widths'] = cw
            ww = u.get('win_width',''); wh = u.get('win_height','')
            defaults['win_width'] = int(ww) if ww.isdigit() else defaults['win_width']
            defaults['win_height'] = int(wh) if wh.isdigit() else defaults['win_height']
    return defaults

def save_settings(api_key, model, system_msg,
                  last_db=None, last_row=None, col_widths=None,
                  win_width=None, win_height=None):
    config = configparser.ConfigParser()
    config['Perplexity'] = {'api_key': api_key, 'model': model}
    user = {'system_msg': system_msg}
    if last_db is not None: user['last_db'] = last_db
    if last_row is not None: user['last_row'] = str(last_row)
    if col_widths: user['col_widths'] = ','.join(f"{c}:{w}" for c,w in col_widths.items())
    if win_width is not None: user['win_width'] = str(win_width)
    if win_height is not None: user['win_height'] = str(win_height)
    config['User'] = user
    with open(SETTINGS_FILE, 'w') as f:
        config.write(f)

class MainWindow(tk.Tk):
    def __init__(self):
        super().__init__(className='Basqled')

        default_font = tkfont.nametofont("TkDefaultFont")
        default_font.configure(size=10, family="DejaVu Sans")

        s = load_settings()
        self.title(f"baSQLed v{verzija} (C) BArko, 2025+")
        self.geometry(f"{s['win_width']}x{s['win_height']}")
        self.minsize(s['win_width'], s['win_height'])

        self.api_key = s['api_key']
        self.model = s['model']
        self.db_path = s['last_db']
        self.last_row = s['last_row']
        self.col_widths = s['col_widths']

        self.sys_label = tk.Label(self, text="Sistemsko sporočilo:")
        self.sys_label.pack(anchor='w', padx=5)
        self.sys_label.bind("<Double-Button-1>", self.napolni_sistemsko_sporocilo)

        self.sys_msg = tk.Text(self, height=4)
        self.sys_msg.insert('1.0', s['system_msg'])
        self.sys_msg.pack(fill='x', padx=5, pady=2)
        
        top = tk.Frame(self)
        tk.Label(top, text="Tabela:").pack(side='left')
        self.table_cb = ttk.Combobox(top, state='readonly')
        self.table_cb.pack(side='left', padx=5)
        self.table_cb.bind('<<ComboboxSelected>>', lambda e: self.load_selected_table())
        tk.Button(top, text="Osveži", command=self.refresh_table).pack(side='left', padx=5)
        top.pack(fill='x', padx=5, pady=5)

        self.tree = ttk.Treeview(self, columns=(), show='headings', selectmode='browse')
        self.tree.bind('<<TreeviewSelect>>', self.on_select_row)
        self.tree.pack(fill='both', expand=True, padx=5)
        self.tree.bind('<Double-1>', self.on_double_click)

        nav = tk.Frame(self)
        for text, cmd in [("🗑️ Izbriši",  self.delete_row),
                          ("Izvozi CSV", self.export_csv)]:
            tk.Button(nav, text=text, command=cmd).pack(side='left', padx=2)
        tk.Label(nav, text="Filtriraj:").pack(side='left', padx=5)
        self.filter_entry = tk.Entry(nav)
        self.filter_entry.pack(side='left')
        self.filter_entry.bind('<Return>', lambda e: self.apply_filter())
        tk.Button(nav, text="Pojdi", command=self.apply_filter).pack(side='left', padx=2)
        self.process_button = tk.Button(nav, text="⚙️ Procesiraj", command=self.process)
        self.process_button.pack(side='left', padx=5)
        nav.pack(fill='x', padx=5, pady=5)

        tk.Label(self, text="Trenutna vsebina:").pack(anchor='w', padx=5)
        self.current_content = tk.Text(self, height=4, state='disabled')
        self.current_content.pack(fill='x', padx=5, pady=2)
        tk.Label(self, text="Perplexity odgovor:").pack(anchor='w', padx=5)
        self.gpt_response = tk.Text(self, height=4, state='disabled')
        self.gpt_response.pack(fill='x', padx=5, pady=2)

        bottom = tk.Frame(self)
        self.sql_button = tk.Button(bottom, text="SQL", command=self.open_sql, state='disabled')
        self.sql_button.pack(side='left', padx=5)
        tk.Button(bottom, text="Pomoč", command=self.open_help).pack(side='right', padx=5)
        tk.Button(bottom, text="Nastavitve", command=self.open_settings).pack(side='right', padx=5)
        tk.Button(bottom, text="Odpri bazo", command=self.open_db).pack(side='right')
        bottom.pack(fill='x', padx=5, pady=5)

        self.conn = None
        self.columns = []
        self.full_rows = []
        self.rowids = []
        if self.db_path and os.path.isfile(self.db_path):
            self.init_db(self.db_path)
        self.protocol('WM_DELETE_WINDOW', self.on_close)

    def init_db(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.text_factory = lambda b: b.decode('utf-8','replace')
        cursor = self.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [r[0] for r in cursor.fetchall()]
        self.table_cb['values'] = tables
        if tables:
            self.table_cb.set(tables[0])
            self.load_selected_table()
        self.sql_button.config(state='normal')

    def napolni_sistemsko_sporocilo(self, event):
        poljubno_besedilo = ("You are a helpful AI assistant. Answer in Slovenian language only. Provide only the final answer. It is important that you do not include any explanation on the steps below. Do not show the intermediate steps information. Do not include references or citations in the format [1], [2], etc. — they must be completely omitted. Enrich the clue '[opis]' based on the answer '[geslo]'. If it refers to a person, verify the birth and death years and write them in the format: ( yyyy - yyyy ) or ( yyyy ). Use factual information from reliable sources (e.g., Wikipedia). Include correct names, titles, companies, and places. Fix any incorrect bracket formatting — use exactly one space after the opening parenthesis, one space before and after the hyphen, and one space before the closing parenthesis. Do not add fictional information. If there is not enough data, return the original clue without explanation. Answer ALL CAPITAL LETTERS, in one short sentence. LECTORATE THE ANSWER BEFORE GIVING IT.")
        self.sys_msg.delete('1.0', tk.END)
        self.sys_msg.insert('1.0', poljubno_besedilo)


    def open_sql(self):
        dlg = tk.Toplevel(self)
        dlg.attributes("-topmost", True)
        dlg.title("Urejevalnik SQL")
        dlg.rowconfigure(0, weight=1)
        dlg.columnconfigure(1, weight=1)

        ln_canvas = tk.Canvas(dlg, width=30, bg='lightgrey')
        ln_canvas.grid(row=0, column=0, sticky='ns')

        sql_text = tk.Text(dlg, height=10, width=80, wrap='none')
        sql_text.grid(row=0, column=1, sticky='nsew', padx=5, pady=5)
        sql_text.focus_set()

        def update_line_numbers(event=None):
            ln_canvas.delete('all')
            i = sql_text.index('@0,0')
            while True:
                dline = sql_text.dlineinfo(i)
                if dline is None:
                    break
                y = dline[1]
                linenum = str(i).split('.')[0]
                ln_canvas.create_text(2, y, anchor='nw', text=linenum)
                i = sql_text.index(f"{i}+1line")

        ln_canvas.bind('<Configure>', update_line_numbers)
        sql_text.bind('<KeyRelease>', update_line_numbers)
        sql_text.bind('<MouseWheel>', update_line_numbers)
        update_line_numbers()

        frm = tk.Frame(dlg)
        frm.grid(row=1, column=0, columnspan=2, sticky='ew', pady=5)

        btn_execute = tk.Button(frm, text='Izvedi SQL')
        btn_cancel = tk.Button(frm, text='Prekliči', command=dlg.destroy)
        btn_execute.pack(side='left', padx=5)
        btn_cancel.pack(side='left', padx=5)

        def execute_sql():
            btn_execute.config(state='disabled')
            sql = sql_text.get('1.0', tk.END).strip()

            critical = ('drop', 'delete', 'alter')
            if any(sql.lower().lstrip().startswith(c) for c in critical):
                ok = messagebox.askyesno(
                    'Potrditev',
                    'Ta ukaz bo spremenil strukturo ali vsebino baze. Nadaljevati?'
                )
                if not ok:
                    btn_execute.config(state='normal')
                    return

            try:
                cursor = self.conn.cursor()
                cursor.execute(sql)

                if sql.lower().lstrip().startswith('select'):
                    rows = cursor.fetchall()
                    cols = [desc[0] for desc in cursor.description]

                    self.tree.delete(*self.tree.get_children())
                    self.tree['columns'] = cols
                    for c in cols:
                        self.tree.heading(c, text=c)
                        self.tree.column(c, width=self.col_widths.get(c, 100))
                    for row in rows:
                        self.tree.insert('', tk.END, values=row)
                else:
                    self.conn.commit()
                    messagebox.showinfo('Uspeh', 'Ukaz SQL je bil uspešno izveden')

            except sqlite3.Error as e:
                messagebox.showerror('Napaka pri izvajanju', str(e))

            finally:
                btn_execute.config(state='normal')
                dlg.destroy()

        btn_execute.config(command=execute_sql)

    def load_selected_table(self):
        self.db_table = self.table_cb.get()
        self.refresh_table()

    def refresh_table(self):
        if not self.conn or not hasattr(self, 'db_table') or not self.db_table:
            self.open_db()
            return
        cursor = self.conn.cursor()
        cursor.execute(f"SELECT rowid,* FROM {self.db_table}")
        rows = cursor.fetchall()
        self.rowids = [r[0] for r in rows]
        self.full_rows = [r[1:] for r in rows]
        cursor.execute(f"PRAGMA table_info({self.db_table})")
        info = cursor.fetchall()
        self.columns = [col[1] for col in info]
        self.tree.delete(*self.tree.get_children())
        self.tree['columns'] = self.columns
        for col in self.columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=self.col_widths.get(col, 100))
        for r in self.full_rows:
            self.tree.insert('', tk.END, values=r)
        if self.last_row is not None and 0 <= self.last_row < len(self.full_rows):
            self.select_focus(self.tree.get_children()[self.last_row])

    def on_double_click(self, event):
        region = self.tree.identify('region', event.x, event.y)
        if region != 'cell':
            return
        item = self.tree.identify_row(event.y)
        col_id = self.tree.identify_column(event.x)
        idx = int(col_id.replace('#','')) - 1
        if not item or idx < 0:
            return
        coln = self.columns[idx]
        if coln in ('geslo', 'id'):
            return
        old = self.tree.item(item)['values'][idx]
        r_i = list(self.tree.get_children()).index(item)
        rid = self.rowids[r_i]
        coln = self.columns[idx]

        dlg = tk.Toplevel(self)
        dlg.attributes("-topmost", True)
        dlg.title(f"Uredi {coln}")
        dlg.rowconfigure(1, weight=1)
        dlg.columnconfigure(0, weight=1)

        tk.Label(dlg, text=f"{coln}:").grid(row=0, column=0, sticky='w', padx=5, pady=5)
        ent = tk.Text(dlg, height=4, width=50)
        ent.insert('1.0', old)
        ent.grid(row=1, column=0, sticky='nsew', padx=5, pady=5)
        ent.focus_set()

        frm = tk.Frame(dlg)
        frm.grid(row=2, column=0, sticky='ew', pady=5)
        def save_edit():            
            nv = ent.get('1.0', tk.END).rstrip('\n').upper()
            try:
                idx_geslo = self.columns.index('geslo')
                geslo = self.full_rows[r_i][idx_geslo]
                spremembe_path = os.path.join(os.path.dirname(SETTINGS_FILE), 'spremembe.txt')
                with open(spremembe_path, 'a', encoding='utf-8') as f:
                    f.write(f"*\n{geslo}\n{old}\n{nv}\n")
                self.conn.execute(f"UPDATE {self.db_table} SET {coln}=? WHERE rowid=?", (nv, rid))
                self.conn.commit()
                self.last_row = r_i
                dlg.destroy()
                self.refresh_table()
            except sqlite3.Error as e:
                messagebox.showerror('Napaka pri shranjevanju', str(e))

        tk.Button(frm, text='Shrani', command=save_edit).pack(side='left', padx=5)
        tk.Button(frm, text='Prekliči', command=dlg.destroy).pack(side='left', padx=5)

    def select_focus(self, item):
        self.tree.selection_set(item)
        self.tree.focus(item)
        self.tree.see(item)
        self.last_row = self.get_row_idx()

    def get_row_idx(self):
        f = self.tree.focus()
        return list(self.tree.get_children()).index(f) if f else None

    def on_select_row(self, event):
        self.last_row = self.get_row_idx()

    def save_col_widths(self):
        self.col_widths = {c: self.tree.column(c, width=None) for c in self.columns}

    def process(self):
        self.process_button.config(state='disabled')
        col = "opis"
        if col not in self.columns:
            messagebox.showerror('Napaka', 'Stolpec "opis" ne obstaja v tabeli.')
            return
        self.save_col_widths()
        save_settings(self.api_key, self.model,
              self.sys_msg.get('1.0', tk.END).strip(),
              self.db_path, self.get_row_idx(), self.col_widths,
              self.winfo_width(), self.winfo_height())

        idx = self.columns.index(col)
        row_idx = self.get_row_idx()
        if row_idx is None:
            messagebox.showwarning('Opozorilo', 'Ni izbrane vrstice')
            return
        row_values = self.tree.item(self.tree.get_children()[row_idx])['values']
        rowid = self.rowids[row_idx]

        try:
            geslo = row_values[self.columns.index('geslo')]
            opis = row_values[self.columns.index('opis')]
        except ValueError:
            messagebox.showerror('Napaka', 'Stolpca \"geslo\" in/ali \"opis\" ne obstajata v tabeli.')
            return

        sys_msg = self.sys_msg.get('1.0', tk.END).strip()
        prompt = sys_msg.replace('[geslo]', str(geslo)).replace('[opis]', str(opis))

        self.current_content.config(state='normal')
        self.current_content.delete('1.0', tk.END)
        self.current_content.insert(tk.END, opis)
        self.current_content.config(state='disabled')

        def process_call():
            odgovor = call_perplexity(sys_msg, prompt, self.api_key, self.model)

            self.gpt_response.config(state='normal')
            self.gpt_response.delete('1.0', tk.END)
            self.gpt_response.insert(tk.END, odgovor)
            self.gpt_response.config(state='disabled')

            ok = messagebox.askyesno("Shrani odgovor", "Ali želiš shraniti spremembo?")
            if ok:
                try:
                    spremembe_path = os.path.join(os.path.dirname(SETTINGS_FILE), 'spremembe.txt')
                    star_opis = self.current_content.get('1.0', tk.END).strip()
                    novi_opis = self.gpt_response.get('1.0', tk.END).strip()
                    with open(spremembe_path, 'a', encoding='utf-8') as f:
                        f.write(f"*\n{geslo}\n{star_opis}\n{novi_opis}\n")
                    self.conn.execute(f"UPDATE {self.db_table} SET {col}=? WHERE rowid=?", (odgovor, rowid))
                    self.conn.commit()
                    self.last_row = row_idx
                    self.refresh_table()
                except sqlite3.Error as e:
                    messagebox.showerror("Napaka pri shranjevanju", str(e))

        threading.Thread(target=lambda: self._call_and_save(sys_msg, prompt, rowid, col, row_idx, geslo), daemon=True).start()

    def _call_and_save(self, sys_msg, prompt, rowid, col, row_idx, geslo):
        odgovor = call_perplexity(sys_msg, prompt, self.api_key, self.model)
        star_opis = self.current_content.get('1.0', tk.END).strip()

        def shrani_v_bazo(popravljen_opis):
            try:
                spremembe_path = os.path.join(os.path.dirname(SETTINGS_FILE), 'spremembe.txt')
                with open(spremembe_path, 'a', encoding='utf-8') as f:
                    f.write(f"*\n{geslo}\n{star_opis}\n{popravljen_opis}\n")
                self.conn.execute(f"UPDATE {self.db_table} SET {col}=? WHERE rowid=?", (popravljen_opis, rowid))
                self.conn.commit()
                self.last_row = row_idx
                self.refresh_table()
            except sqlite3.Error as e:
                messagebox.showerror("Napaka pri shranjevanju", str(e))

        def update_ui():
            self.gpt_response.config(state='normal')
            self.gpt_response.delete('1.0', tk.END)
            self.gpt_response.insert(tk.END, odgovor)
            self.gpt_response.config(state='disabled')
            self.potrdi_in_uredi(star_opis, odgovor, shrani_v_bazo)

        self.after(0, update_ui)
        self.after(0, lambda: self.process_button.config(state='normal'))

    def open_db(self):
        p = filedialog.askopenfilename(filetypes=[('SQLite','*.db *.sqlite'),('Vse datoteke','*')])
        if p:
            self.db_path = p
            self.init_db(p)
    
    def open_help(self):
        dlg = tk.Toplevel(self)
        dlg.attributes("-topmost", True)
        dlg.title("Pomoč")

        dlg.rowconfigure(0, weight=1)
        dlg.columnconfigure(0, weight=1)

        text = tk.Text(dlg, height=24)
        text.insert('1.0', f"baSQLed v{verzija}\n\n"                            
                            "Orodje za manipulacijo podatkovnega skladišča Ugankarskega Asistenta\n\n"
                            "Možnosti orodja:\n\n"
                            "- sistemsko sporočilo s katerim povemo kaj želimo od AI, uporabimo lahko dva parametra in to sta [geslo] in [opis].\n"
                            "- izbiro ua baze in njen prikaz vsebine\n"
                            "- dvoklik na polje 'opis' odpre okno za urejanje vsebine polja\n"
                            "- možnost pomika na vrh, dno, gor in dol po vrsticah\n"
                            "- izvoz v csv\n"
                            "- filtriranje po vseh poljih\n"                            
                            "- procesiraj pa pomeni preprosto: pošiljanje vsebine izbranega polja na AI, AI pa vrne odgovor, ki ga lahko shranimo ali pa tudi ne v bazo\n"
                            "- izvajanje SQL ukazov, kot so SELECT, INSERT, DELETE in podobno\n"
                            "- v nastavitvah vnesemo API ključ in model s katerim bomo delali\n\n"
                            "Dvoklik na besedilo 'Sistemsko sporočilo' bo prepisal vaš trenutni sistemski prompt.\n\n"
                            "Avtor: BArko")
        text.config(state='disabled')
        text.grid(row=0, column=0, sticky='nsew', padx=10, pady=10)

        btn_close = tk.Button(dlg, text="Zapri", command=dlg.destroy)
        btn_close.grid(row=1, column=0, pady=(0,10))

    def open_settings(self):
        dlg = tk.Toplevel(self)
        dlg.attributes("-topmost", True)
        dlg.title('Nastavitve')
        dlg.resizable(True, False)

        dlg.rowconfigure(0, weight=1)
        dlg.rowconfigure(1, weight=1)
        dlg.rowconfigure(2, weight=0)
        dlg.columnconfigure(1, weight=1)

        tk.Label(dlg, text='API ključ:').grid(row=0, column=0, sticky='w', padx=5, pady=5)
        ke = tk.Entry(dlg, width=50)
        ke.insert(0, self.api_key)
        ke.grid(row=0, column=1, sticky='ew', padx=5, pady=5)

        tk.Label(dlg, text='Model:').grid(row=1, column=0, sticky='w', padx=5, pady=5)
        me = tk.Entry(dlg, width=50)
        me.insert(0, self.model)
        me.grid(row=1, column=1, sticky='ew', padx=5, pady=5)

        def save_conf():
            self.api_key = ke.get().strip()
            self.model = me.get().strip()
            save_settings(self.api_key, self.model,
                          self.sys_msg.get('1.0', tk.END).strip(),
                          self.db_path, self.get_row_idx(), self.col_widths,
                          self.winfo_width(), self.winfo_height())
            dlg.destroy()

        btn_frm = tk.Frame(dlg)
        btn_frm.grid(row=2, column=0, columnspan=2, sticky='ew', pady=5, padx=5)

        tk.Button(btn_frm, text='Shrani', command=save_conf).pack(side='left', padx=5)
        tk.Button(btn_frm, text='Prekliči', command=dlg.destroy).pack(side='left', padx=5)

    def export_csv(self):
        if not self.full_rows or not self.columns:
            messagebox.showwarning("Opozorilo", "Ni podatkov za izvoz!")
            return
        p = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[('CSV','*.csv')])
        if not p:
            return
        try:
            with open(p, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(self.columns)
                writer.writerows(self.full_rows)
            messagebox.showinfo("Uspeh", f"Podatki uspešno izvoženi v {p}")
        except Exception as e:
            messagebox.showerror("Napaka", str(e))

    def delete_row(self):
        """Izbriše izbrano vrstico iz Treeview in baze ter zapiše geslo in opis v spremembe.txt."""
        sel = self.tree.focus()
        if not sel:
            messagebox.showwarning("Opozorilo", "Ni izbrane vrstice za brisanje!")
            return
        
        if not messagebox.askyesno("Potrditev brisanja", "Ali res želiš izbrisati izbrano vrstico?"):
            return

        idx = list(self.tree.get_children()).index(sel)
        rid = self.rowids[idx]
        values = self.full_rows[idx]

        try:
            geslo = values[self.columns.index('geslo')]
            opis  = values[self.columns.index('opis')]
        except ValueError:
            messagebox.showerror("Napaka", "Stolpca 'geslo' ali 'opis' ne obstajata!")
            return

        spremembe_path = os.path.join(os.path.dirname(SETTINGS_FILE), 'spremembe.txt')
        with open(spremembe_path, 'a', encoding='utf-8') as f:
            f.write("-\n")
            f.write(f"{geslo}\n")
            f.write(f"{opis}\n")

        try:
            self.conn.execute(f"DELETE FROM {self.db_table} WHERE rowid = ?", (rid,))
            self.conn.commit()
            
            old_idx = self.last_row if self.last_row is not None else 0  # Zapomni si staro pozicijo
            self.refresh_table()
            
            ch = self.tree.get_children()
            if ch:
                # Izberi naslednjo vrstico, če obstaja, sicer prejšnjo
                next_idx = min(old_idx, len(ch) - 1)
                self.select_focus(ch[next_idx])
        except sqlite3.Error as e:
            messagebox.showerror("Napaka pri brisanju", str(e))


    def apply_filter(self):
        ftxt = self.filter_entry.get().strip()
        if not ftxt:
            self.refresh_table()
            return
        filtered = []
        for r in self.full_rows:
            if any(ftxt.lower() in str(val).lower() for val in r):
                filtered.append(r)
        self.tree.delete(*self.tree.get_children())
        for r in filtered:
            self.tree.insert('', tk.END, values=r)

    def on_close(self):
        self.save_col_widths()
        save_settings(self.api_key, self.model,
              self.sys_msg.get('1.0', tk.END).strip(),
              self.db_path, self.get_row_idx(), self.col_widths,
              self.winfo_width(), self.winfo_height())
        self.destroy()

    def potrdi_in_uredi(self, star_opis, novi_opis, shrani_callback):
        dlg = tk.Toplevel(self)
        dlg.attributes("-topmost", True)
        dlg.title("Shrani odgovor")
        dlg.grab_set()

        dlg.rowconfigure(1, weight=1)
        dlg.columnconfigure(0, weight=1)

        tk.Label(dlg, text="Uredi besedilo pred shranjevanjem:").grid(row=0, column=0, sticky='w', padx=8, pady=(10,2))

        text_edit = tk.Text(dlg, width=70, height=5)
        text_edit.grid(row=1, column=0, sticky='nsew', padx=8, pady=4)
        text_edit.insert('1.0', novi_opis)
        text_edit.focus_set()

        btn_frame = tk.Frame(dlg)
        btn_frame.grid(row=2, column=0, pady=8)

        def potrdi():
            popravljen_opis = text_edit.get('1.0', tk.END).strip().upper()
            shrani_callback(popravljen_opis)
            dlg.destroy()

        def preklici():
            dlg.destroy()

        tk.Button(btn_frame, text="Shrani", command=potrdi).pack(side='left', padx=5)
        tk.Button(btn_frame, text="Prekliči", command=preklici).pack(side='left', padx=5)

    
if __name__ == '__main__':
    MainWindow().mainloop()
