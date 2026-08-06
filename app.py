#!/usr/bin/env python3
"""Boxtray Sales System - Flask Backend (SQLite local / PostgreSQL cloud)"""
import os, csv, json, re, smtplib, dns.resolver, socket, threading
from datetime import datetime, timedelta
from pathlib import Path
from email.mime.text import MIMEText
from email.utils import formatdate
from flask import Flask, request, jsonify, g, session, redirect, url_for
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

BASE = Path(__file__).parent
SALES_DIR = BASE.parent if BASE.name == 'web_app' else Path(os.getcwd())

DATABASE_URL = os.environ.get('DATABASE_URL', '')
IS_RENDER = bool(os.environ.get('RENDER', '')) or bool(os.environ.get('PORT', ''))

# On Render, use Supabase pooler (IPv4, works)
if IS_RENDER:
    DATABASE_URL = 'postgresql://postgres.eptlcvlsdvpuqdltzbfr:001745Xiaoming@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres'

USE_PG = bool(DATABASE_URL) if not IS_RENDER else True

if USE_PG:
    import psycopg2
    import psycopg2.extras
else:
    import sqlite3

app = Flask(__name__, static_folder='static', static_url_path='/static')
app.secret_key = os.environ.get('SECRET_KEY', 'boxtray-sales-secret-key-2026')
CORS(app)

# ------- Config -------
SMTP_HOST = os.environ.get('SMTP_HOST', 'smtp.office365.com')
SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
FROM_EMAIL = os.environ.get('FROM_EMAIL', 'sales@boxtray.com')
FROM_PASS = os.environ.get('FROM_PASS', 'BTG&2026')
APPROVERS = os.environ.get('APPROVERS', 'karen.zhang@boxtray.com,roy@boxtray.com').split(',')
NOW_SQL = 'NOW()' if USE_PG else "datetime('now','localtime')"
RETURNING = ' RETURNING id' if USE_PG else ''

def get_db():
    if 'db' not in g:
        if USE_PG:
            g.db = psycopg2.connect(DATABASE_URL, sslmode='require')
            g.db.cursor_factory = psycopg2.extras.RealDictCursor
        else:
            if IS_RENDER:
                import tempfile, shutil
                DB_PATH = Path(tempfile.gettempdir()) / 'crm.db'
                if not DB_PATH.exists():
                    shutil.copy(BASE / 'crm.db', DB_PATH)
            else:
                DB_PATH = BASE / 'data' / 'crm.db'
                os.makedirs(BASE / 'data', exist_ok=True)
            g.db = sqlite3.connect(str(DB_PATH))
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA journal_mode=WAL")
    return g.db

@app.teardown_appcontext
def close_db(exc):
    db = g.pop('db', None)
    if db: db.close()

def pg_adapt(sql, params):
    if USE_PG:
        sql = sql.replace('?', '%s')
        # PostgreSQL bool compat: convert integer bools
        sql = sql.replace('is_blacklisted=0','is_blacklisted=FALSE')
        sql = sql.replace('is_blacklisted=1','is_blacklisted=TRUE')
        sql = sql.replace('added_to_crm=0','added_to_crm=FALSE')
        sql = sql.replace('added_to_crm=1','added_to_crm=TRUE')
        sql = sql.replace('is_blacklisted != 0','is_blacklisted!=TRUE')
        sql = sql.replace('is_blacklisted  != 1','is_blacklisted!=FALSE')
        cursor = get_db().cursor()
        cursor.execute(sql, params)
        get_db().commit()
        return cursor
    else:
        sql = sql.replace('SERIAL PRIMARY KEY', 'INTEGER PRIMARY KEY AUTOINCREMENT')
        sql = sql.replace('TIMESTAMP DEFAULT NOW()', "TEXT DEFAULT (datetime('now','localtime'))")
        sql = sql.replace('BOOLEAN DEFAULT FALSE', 'INTEGER DEFAULT 0')
        sql = sql.replace('FLOAT', 'REAL')
        return get_db().execute(sql, params)

def qr(sql, params=()):
    """Execute and return all rows."""
    return pg_adapt(sql, params).fetchall()

def q1(sql, params=()):
    """Execute and return one row."""
    return pg_adapt(sql, params).fetchone()

def qi(sql, params=()):
    """Execute INSERT and return new ID."""
    cur = pg_adapt(sql, params)
    if USE_PG:
        row = cur.fetchone()
        return row['id'] if isinstance(row, dict) else row[0]
    else:
        return get_db().execute("SELECT last_insert_rowid()").fetchone()[0]

def q(sql, params=()):
    """Execute without returning rows."""
    pg_adapt(sql, params)

# ------- Init -------
def init_db():
    db = get_db()
    def ex(sql):
        try:
            if USE_PG:
                db.cursor().execute(sql)
            else:
                sql2 = sql.replace('SERIAL PRIMARY KEY', 'INTEGER PRIMARY KEY AUTOINCREMENT')
                sql2 = sql2.replace('TIMESTAMP DEFAULT NOW()', "TEXT DEFAULT (datetime('now','localtime'))")
                sql2 = sql2.replace('BOOLEAN DEFAULT FALSE', 'INTEGER DEFAULT 0')
                sql2 = sql2.replace('FLOAT', 'REAL')
                db.executescript(sql2)
        except: pass

    ex("CREATE TABLE IF NOT EXISTS customers (id SERIAL PRIMARY KEY,company TEXT NOT NULL,email TEXT DEFAULT '',contact_name TEXT DEFAULT '',title TEXT DEFAULT '',phone TEXT DEFAULT '',address TEXT DEFAULT '',country TEXT DEFAULT '',region TEXT DEFAULT '',website TEXT DEFAULT '',source TEXT DEFAULT 'web_search',channel TEXT DEFAULT '',grade TEXT DEFAULT 'C',tier TEXT DEFAULT '',score FLOAT DEFAULT 0,status TEXT DEFAULT 'new',tags TEXT DEFAULT '',company_bio TEXT DEFAULT '',main_products TEXT DEFAULT '',product_fit TEXT DEFAULT '',email_department TEXT DEFAULT '',customer_base TEXT DEFAULT '',scale TEXT DEFAULT '',founded TEXT DEFAULT '',notes TEXT DEFAULT '',is_blacklisted BOOLEAN DEFAULT FALSE,blacklist_reason TEXT DEFAULT '',created_at TIMESTAMP DEFAULT NOW(),updated_at TIMESTAMP DEFAULT NOW(),email_validated TEXT DEFAULT 'No',email_validation_date TEXT DEFAULT '',email_sent_date TEXT DEFAULT '',preview_sent_date TEXT DEFAULT '',template_used TEXT DEFAULT '',follow_up_count INTEGER DEFAULT 0,last_follow_up_date TEXT DEFAULT '',reply_received TEXT DEFAULT 'No',reply_date TEXT DEFAULT '',reply_summary TEXT DEFAULT '',next_task_date TEXT DEFAULT '',task_notes TEXT DEFAULT '')")
    ex("CREATE TABLE IF NOT EXISTS emails (id SERIAL PRIMARY KEY,customer_id INTEGER,to_email TEXT NOT NULL,subject TEXT,body TEXT,type TEXT DEFAULT 'outreach',status TEXT DEFAULT 'sent',sent_at TIMESTAMP DEFAULT NOW(),reply_text TEXT DEFAULT '')")
    ex("CREATE TABLE IF NOT EXISTS tasks (id SERIAL PRIMARY KEY,customer_id INTEGER,title TEXT NOT NULL,due_date TEXT,type TEXT DEFAULT 'follow_up',status TEXT DEFAULT 'pending',notes TEXT DEFAULT '',created_at TIMESTAMP DEFAULT NOW())")
    ex("CREATE TABLE IF NOT EXISTS search_requests (id SERIAL PRIMARY KEY,keywords TEXT NOT NULL,region TEXT DEFAULT '',status TEXT DEFAULT 'pending',created_at TIMESTAMP DEFAULT NOW(),processed_at TIMESTAMP)")
    ex("CREATE TABLE IF NOT EXISTS search_leads (id SERIAL PRIMARY KEY,search_id INTEGER,company TEXT NOT NULL,email TEXT DEFAULT '',contact_name TEXT DEFAULT '',title TEXT DEFAULT '',phone TEXT DEFAULT '',address TEXT DEFAULT '',country TEXT DEFAULT '',region TEXT DEFAULT '',website TEXT DEFAULT '',source TEXT DEFAULT '',notes TEXT DEFAULT '',email_validated TEXT DEFAULT 'No',validation_detail TEXT DEFAULT '',added_to_crm BOOLEAN DEFAULT FALSE)")
    ex("CREATE TABLE IF NOT EXISTS templates (id SERIAL PRIMARY KEY,name TEXT UNIQUE NOT NULL,subject TEXT DEFAULT '',body TEXT DEFAULT '',folder TEXT DEFAULT 'general',created_at TIMESTAMP DEFAULT NOW(),updated_at TIMESTAMP DEFAULT NOW())")
    ex("CREATE TABLE IF NOT EXISTS users (id SERIAL PRIMARY KEY,username TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,role TEXT DEFAULT 'user',created_at TIMESTAMP DEFAULT NOW())")
    if USE_PG: db.commit()

    # Seed default admin
    if not q1("SELECT id FROM users WHERE username='admin'"):
        q("INSERT INTO users (username,password_hash,role) VALUES (?,?,?)",('admin',generate_password_hash('admin123'),'admin'))

def migrate_csv():
    existing = q1("SELECT COUNT(*) as cnt FROM customers")
    if existing and existing['cnt'] > 0: return
    csv_path = SALES_DIR / '07-crm' / 'crm_master.csv'
    if not csv_path.exists(): return
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            q("INSERT INTO customers (company,email,contact_name,title,phone,address,country,region,website,source,grade,tier,score,status,notes,company_bio,main_products,product_fit,email_department,customer_base,scale,founded,is_blacklisted,email_validated,email_validation_date,email_sent_date,preview_sent_date,template_used,follow_up_count,last_follow_up_date,reply_received,reply_date,reply_summary) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?,?,?,?,?,?)",
              (row.get('company',''),row.get('email',''),row.get('contact_name',''),row.get('title',''),row.get('phone',''),row.get('address',''),row.get('country',''),row.get('region',''),row.get('website',''),row.get('source','web_search'),'C',row.get('tier',''),float(row.get('score',0)or 0),'sent' if row.get('email_sent_date','').strip() else 'new',row.get('notes',''),row.get('company_bio',''),row.get('main_products',''),row.get('product_fit',''),row.get('email_department',''),row.get('customer_base',''),row.get('scale',''),row.get('founded',''),row.get('email_validated','No'),row.get('email_validation_date',''),row.get('email_sent_date',''),row.get('preview_sent_date',''),row.get('template_used',''),int(row.get('follow_up_count',0)or 0),row.get('last_follow_up_date',''),row.get('reply_received','No'),row.get('reply_date',''),row.get('reply_summary','')))

# ------- Email -------
def smtp_probe(email):
    try: user, domain = email.split('@')
    except: return False, 'invalid_format'
    try:
        answers = dns.resolver.resolve(domain, 'MX')
        mx = sorted([(r.preference, str(r.exchange).rstrip('.')) for r in answers], key=lambda x: x[0])
    except: return False, 'no_mx'
    for _, host in mx:
        try:
            with smtplib.SMTP(host, 25, timeout=6) as s:
                s.ehlo_or_helo_if_needed()
                code, _ = s.mail(FROM_EMAIL)
                if code != 250: continue
                code, _ = s.rcpt(email)
                if code == 250: return True, 'valid'
                elif code == 550: return False, 'not_found'
        except: continue
    return None, 'mx_fail'

def send_smtp(to, subject, body):
    msg = MIMEText(body, 'plain', 'utf-8')
    msg['From'] = FROM_EMAIL
    msg['To'] = to if isinstance(to, str) else ', '.join(to)
    msg['Subject'] = subject
    msg['Date'] = formatdate()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
        s.starttls()
        s.login(FROM_EMAIL, FROM_PASS)
        s.send_message(msg)
    return True

# ------- Auth -------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'error':'Unauthorized'}), 401
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'error':'Unauthorized'}), 401
            return redirect('/login')
        if session.get('role') != 'admin':
            return jsonify({'error':'Forbidden'}), 403
        return f(*args, **kwargs)
    return decorated

@app.route('/login')
def login_page():
    if 'user_id' in session:
        return redirect('/')
    return app.send_static_file('login.html')

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json
    username = (data.get('username') or '').strip()
    password = data.get('password', '')
    user = q1("SELECT * FROM users WHERE username=?",(username,))
    if not user or not check_password_hash(user['password_hash'], password):
        return jsonify({'error':'Invalid credentials'}), 401
    session['user_id'] = user['id']
    session['username'] = user['username']
    session['role'] = user['role']
    return jsonify({'ok':True,'username':user['username'],'role':user['role']})

@app.route('/api/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'ok':True})

@app.route('/api/auth')
def api_auth():
    if 'user_id' not in session:
        return jsonify({'logged_in':False})
    return jsonify({'logged_in':True,'username':session.get('username'),'role':session.get('role')})

# ------- Routes -------
@app.route('/')
@login_required
def index():
    return app.send_static_file('index.html')

@app.route('/api/dashboard')
def dashboard():
    t = q1("SELECT COUNT(*) as cnt FROM customers WHERE is_blacklisted=0")['cnt']
    s = q1("SELECT COUNT(*) as cnt FROM customers WHERE email_sent_date != '' AND is_blacklisted=0")['cnt']
    r = q1("SELECT COUNT(*) as cnt FROM customers WHERE reply_received='Yes' AND is_blacklisted=0")['cnt']
    b = q1("SELECT COUNT(*) as cnt FROM customers WHERE is_blacklisted=1")['cnt']
    return jsonify({'total':t,'sent':s,'replied':r,'new':t-s,'blacklisted':b,
        'regions':[{'name':x['region'],'count':x['cnt']} for x in qr("SELECT region, COUNT(*) as cnt FROM customers WHERE is_blacklisted=0 GROUP BY region")],
        'channels':[{'name':x['ch'],'count':x['cnt']} for x in qr("SELECT COALESCE(NULLIF(channel,''), source) as ch, COUNT(*) as cnt FROM customers WHERE is_blacklisted=0 GROUP BY ch")],
        'grades':[{'name':x['grade'],'count':x['cnt']} for x in qr("SELECT grade, COUNT(*) as cnt FROM customers WHERE is_blacklisted=0 GROUP BY grade")]})


@login_required
@app.route('/api/customers')
def list_customers():
    grade = request.args.get('grade',''); region = request.args.get('region','')
    status = request.args.get('status',''); search = request.args.get('search','')
    bl = request.args.get('blacklisted','')
    parts = ["SELECT * FROM customers WHERE 1=1"]; params = []
    if grade: parts.append("AND grade=?"); params.append(grade)
    if region: parts.append("AND region=?"); params.append(region)
    if status == 'sent': parts.append("AND email_sent_date != ''")
    elif status == 'new': parts.append("AND email_sent_date = ''")
    elif status == 'replied': parts.append("AND reply_received='Yes'")
    if bl == '1': parts.append("AND is_blacklisted=1")
    else: parts.append("AND is_blacklisted=0")
    if search: p=f'%{search}%'; parts.append("AND (company LIKE ? OR email LIKE ? OR contact_name LIKE ?)"); params.extend([p,p,p])
    parts.append("ORDER BY created_at DESC")
    return jsonify([dict(r) for r in qr(' '.join(parts), params)])


@login_required
@app.route('/api/customers/<int:id>')
def get_customer(id):
    row = q1("SELECT * FROM customers WHERE id=?", (id,))
    if not row: return jsonify({'error':'Not found'}), 404
    return jsonify({'customer':dict(row), 'emails':[dict(e) for e in qr("SELECT * FROM emails WHERE customer_id=? ORDER BY sent_at DESC",(id,))],
        'tasks':[dict(t) for t in qr("SELECT * FROM tasks WHERE customer_id=? ORDER BY due_date ASC",(id,))]})


@login_required
@app.route('/api/customers/<int:id>', methods=['PUT'])
def update_customer(id):
    data = request.json
    allowed = ['company','email','contact_name','title','phone','address','country','region','website','source','channel','grade','tier','score','status','tags','notes','company_bio','main_products','product_fit','email_department','customer_base','scale','founded','is_blacklisted','blacklist_reason','reply_received','reply_date','reply_summary','next_task_date','task_notes']
    sets = [f + '=?' for f in allowed if f in data]
    vals = [data[f] for f in allowed if f in data]
    if not sets: return jsonify({'ok':False})
    sets.append('updated_at=' + NOW_SQL)
    vals.append(id)
    q(f"UPDATE customers SET {', '.join(sets)} WHERE id=?", vals)
    return jsonify({'ok':True})


@login_required
@app.route('/api/customers', methods=['POST'])
def add_customer():
    data = request.json
    email = data.get('email','').strip(); company = data.get('company','').strip()
    if email and q1("SELECT id FROM customers WHERE email=?",(email,)):
        return jsonify({'error':f'Duplicate: {email}'}), 409
    fields = ['company','email','contact_name','title','phone','address','country','region','website','source','channel','grade','notes','company_bio','main_products','product_fit','email_department','customer_base','scale','founded']
    vals = [data.get(f,'') for f in fields]
    vals[9] = vals[9] or 'web_search'; vals[11] = vals[11] or 'C'
    ph = ','.join('?' * len(fields))
    new_id = qi(f"INSERT INTO customers ({','.join(fields)}) VALUES ({ph}){RETURNING}", vals)
    return jsonify({'ok':True,'id':new_id})


@login_required
@app.route('/api/customers/<int:id>', methods=['DELETE'])
def delete_customer(id):
    q("UPDATE customers SET is_blacklisted=1, blacklist_reason='deleted' WHERE id=?",(id,))
    return jsonify({'ok':True})


@login_required
@app.route('/api/customers/batch', methods=['POST'])
def batch_action():
    data = request.json; ids = data.get('ids',[]); action = data.get('action',''); value = data.get('value','')
    if not ids: return jsonify({'error':'No ids'}), 400
    ph = ','.join('?'*len(ids))
    if action == 'grade': q(f"UPDATE customers SET grade=?, updated_at={NOW_SQL} WHERE id IN ({ph})", [value]+ids)
    elif action == 'blacklist': q(f"UPDATE customers SET is_blacklisted=1, blacklist_reason=? WHERE id IN ({ph})", [value]+ids)
    elif action == 'unblacklist': q(f"UPDATE customers SET is_blacklisted=0, blacklist_reason='' WHERE id IN ({ph})", ids)
    elif action == 'add_tag':
        for i in ids:
            row = q1("SELECT tags FROM customers WHERE id=?",(i,))
            tags = set((row['tags'] or '').split(',')) if row else set()
            tags.add(value); q("UPDATE customers SET tags=? WHERE id=?",(','.join(filter(None,tags)),i))
    elif action == 'remove_tag':
        for i in ids:
            row = q1("SELECT tags FROM customers WHERE id=?",(i,))
            tags = set((row['tags'] or '').split(',')) if row else set()
            tags.discard(value); q("UPDATE customers SET tags=? WHERE id=?",(','.join(filter(None,tags)),i))
    return jsonify({'ok':True})


@login_required
@app.route('/api/validate-email', methods=['POST'])
def validate_email():
    email = request.json.get('email','').strip()
    if not email: return jsonify({'valid':False,'detail':'empty'})
    v,d = smtp_probe(email); return jsonify({'valid':v,'detail':d})


@login_required
@app.route('/api/send-email', methods=['POST'])
def send_email_api():
    data = request.json; cid = data.get('customer_id'); to = data.get('to','')
    subj = data.get('subject',''); body = data.get('body',''); stype = data.get('type','outreach')
    if not data.get('skip_approval'):
        row = q1("SELECT * FROM customers WHERE id=?",(cid,))
        send_smtp(APPROVERS, f"[APPROVAL] -> {row['company']} | {subj}", f"APPROVAL REQUIRED\nTO: {row['company']} <{to}>\n\n---\n{body}\n---\n\nReply APPROVE to send.")
        q(f"UPDATE customers SET preview_sent_date={NOW_SQL}, status='awaiting_approval', template_used=? WHERE id=?", (subj,cid))
        q("INSERT INTO emails (customer_id,to_email,subject,body,type,status) VALUES (?,?,?,?,?,'approval_pending')", (cid,to,subj,body,stype))
        return jsonify({'ok':True,'message':'Approval preview sent'})
    send_smtp(to, subj, body)
    q(f"UPDATE customers SET email_sent_date={NOW_SQL}, status='sent', follow_up_count=follow_up_count+1 WHERE id=?", (cid,))
    q("INSERT INTO emails (customer_id,to_email,subject,body,type,status) VALUES (?,?,?,?,?,'sent')", (cid,to,subj,body,stype))
    return jsonify({'ok':True,'message':f'Sent to {to}'})


@login_required
@app.route('/api/send-approvals', methods=['POST'])
def send_approvals():
    ids = request.json.get('ids',[]); sent=0
    for cid in ids:
        row = q1("SELECT * FROM customers WHERE id=?",(cid,))
        if not row: continue
        le = q1("SELECT * FROM emails WHERE customer_id=? ORDER BY id DESC LIMIT 1",(cid,))
        if not le: continue
        send_smtp(le['to_email'], le['subject'], le['body'])
        q(f"UPDATE customers SET email_sent_date={NOW_SQL}, status='sent', follow_up_count=follow_up_count+1 WHERE id=?",(cid,))
        q(f"UPDATE emails SET status='sent', sent_at={NOW_SQL} WHERE id=?",(le['id'],))
        sent += 1
    return jsonify({'ok':True,'sent':sent})


@login_required
@app.route('/api/tasks', methods=['GET'])
def list_tasks():
    return jsonify([dict(r) for r in qr("SELECT t.*, c.company FROM tasks t LEFT JOIN customers c ON t.customer_id=c.id ORDER BY t.due_date ASC")])


@login_required
@app.route('/api/tasks', methods=['POST'])
def add_task():
    d = request.json
    q("INSERT INTO tasks (customer_id,title,due_date,type,notes) VALUES (?,?,?,?,?)", (d.get('customer_id'),d['title'],d.get('due_date'),d.get('type','follow_up'),d.get('notes','')))
    return jsonify({'ok':True})


@login_required
@app.route('/api/tasks/<int:id>', methods=['PUT'])
def update_task(id):
    d = request.json
    q("UPDATE tasks SET status=?, notes=? WHERE id=?", (d.get('status','pending'),d.get('notes',''),id))
    return jsonify({'ok':True})


@login_required
@app.route('/api/generate-tasks', methods=['POST'])
def generate_tasks():
    rows = qr("SELECT * FROM customers WHERE email_sent_date != '' AND is_blacklisted=0")
    tmps = [(3,'Day 3 Light Touch'),(7,'Day 7 Value Add'),(14,'Day 14 Last Attempt'),(30,'Day 30 Closing Loop')]
    count = 0
    for row in rows:
        if row['email_sent_date']:
            sd = datetime.strptime(str(row['email_sent_date'])[:10],'%Y-%m-%d')
            for days,title in tmps:
                due = (sd+timedelta(days=days)).strftime('%Y-%m-%d')
                if not q1("SELECT id FROM tasks WHERE customer_id=? AND title=? AND due_date=?",(row['id'],title,due)):
                    q("INSERT INTO tasks (customer_id,title,due_date,type) VALUES (?,?,?,?)",(row['id'],title,due,'follow_up'))
                    count += 1
    return jsonify({'ok':True,'created':count})

def seed_templates():
    """Import templates from MD files into DB if empty."""
    if q1("SELECT COUNT(*) as cnt FROM templates")['cnt'] > 0: return
    td = (BASE / 'email-templates') if (BASE / 'email-templates').exists() else (SALES_DIR / '03-email-templates')
    for folder in ['01-cold-outreach','02-follow-up','03-reply-handling']:
        fp = td / folder
        if fp.exists():
            for f in sorted(fp.glob('*.md')):
                c = f.read_text(encoding='utf-8'); subj = ''
                for line in c.split('\n'):
                    if line.startswith('Subject:'): subj = line[8:].strip(); break
                name = f'{folder}/{f.stem}'
                q("INSERT INTO templates (name,subject,body,folder) VALUES (?,?,?,?)",(name,subj,c,folder))


@login_required
@app.route('/api/templates')
def list_templates():
    seed_templates()  # seed on-demand if empty
    return jsonify([dict(r) for r in qr("SELECT * FROM templates ORDER BY folder, name")])


@login_required
@app.route('/api/templates/<path:name>', methods=['PUT'])
def save_template(name):
    from urllib.parse import unquote
    name = unquote(name)
    data = request.json
    seed_templates()  # ensure seeded
    existing = q1("SELECT id FROM templates WHERE name=?",(name,))
    if existing:
        q(f"UPDATE templates SET subject=?, body=?, updated_at={NOW_SQL} WHERE name=?",(data.get('subject',''),data.get('body',''),name))
    else:
        q("INSERT INTO templates (name,subject,body,folder) VALUES (?,?,?,?)",(name,data.get('subject',''),data.get('body',''),data.get('folder','general')))
    return jsonify({'ok':True})


@login_required
@app.route('/api/templates/render', methods=['POST'])
def render_template():
    d = request.json; cid = d.get('customer_id'); tp = d.get('template','')
    row = q1("SELECT * FROM customers WHERE id=?",(cid,))
    if not row: return jsonify({'error':'Not found'}), 404
    name = (row['contact_name'] or '').split()[0] if row['contact_name'] else 'there'
    tpl = q1("SELECT * FROM templates WHERE name=?",(tp,))
    if not tpl: return jsonify({'error':'Template not found'}), 404
    content = tpl['body'].replace('[First Name]',name)
    subj = ''; blines = []; found = False
    for line in content.split('\n'):
        if line.startswith('Subject:'): subj = line[8:].strip(); found = True
        elif found: blines.append(line)
    if not found: blines = content.split('\n')
    return jsonify({'subject':subj,'body':'\n'.join(blines).strip(),'raw_body':tpl['body']})


@login_required
@app.route('/api/channels', methods=['GET'])
def list_channels():
    return jsonify([dict(r) for r in qr("SELECT COALESCE(NULLIF(channel,''), source) as name, COUNT(*) as count FROM customers WHERE is_blacklisted=0 GROUP BY name")])


@login_required
@app.route('/api/duplicates', methods=['GET'])
def check_duplicates():
    if USE_PG:
        ed = qr("SELECT email, COUNT(*) as cnt, STRING_AGG(id::text,',') as ids FROM customers WHERE email != '' AND is_blacklisted=0 GROUP BY email HAVING COUNT(*)>1")
        cd = qr("SELECT company, COUNT(*) as cnt, STRING_AGG(id::text,',') as ids FROM customers WHERE is_blacklisted=0 GROUP BY company HAVING COUNT(*)>1")
    else:
        ed = qr("SELECT email, COUNT(*) as cnt, GROUP_CONCAT(id) as ids FROM customers WHERE email != '' AND is_blacklisted=0 GROUP BY email HAVING cnt>1")
        cd = qr("SELECT company, COUNT(*) as cnt, GROUP_CONCAT(id) as ids FROM customers WHERE is_blacklisted=0 GROUP BY company HAVING cnt>1")
    return jsonify({'email_dupes':[{'email':r['email'],'ids':r['ids']} for r in ed],'company_dupes':[{'company':r['company'],'ids':r['ids']} for r in cd]})


@login_required
@app.route('/api/blacklist')
def list_blacklist():
    return jsonify([dict(r) for r in qr("SELECT * FROM customers WHERE is_blacklisted=1 ORDER BY updated_at DESC")])

# ------- AI Search -------
def extract_email(text):
    m = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
    return m[0] if m else ''

def extract_phone(text):
    m = re.findall(r'[\+]?\d[\d\s\-\(\)]{7,}', text)
    return m[0].strip() if m else ''

def _web_search(query, max_results=5, timeout=15):
    """Search DuckDuckGo Lite HTML (no library dependency)."""
    import sys, urllib.request, urllib.parse, ssl
    try:
        url = 'https://lite.duckduckgo.com/lite/'
        data = urllib.parse.urlencode({'q': query}).encode()
        req = urllib.request.Request(url, data=data, method='POST')
        req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        ctx = ssl.create_default_context()
        resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        html = resp.read().decode('utf-8', errors='replace')
    except Exception as e:
        sys.stderr.write(f'[DDG] HTTP failed: {e}\n')
        return []

    # Parse results: <a href="URL">Title</a> ... <td class="result-snippet">snippet</td>
    results = []
    links = re.findall(r"<a[^>]+href=[\"'](https?://[^\"']+)[\"'][^>]*>(.*?)</a>", html, re.DOTALL)
    snippets = re.findall(r"class=[\"']result-snippet[\"']>(.*?)</td>", html, re.DOTALL)

    for i in range(min(len(links), len(snippets), max_results)):
        href = links[i][0]
        title = re.sub(r'<[^>]+>', '', links[i][1]).strip()
        snippet = re.sub(r'<[^>]+>', '', snippets[i]).strip()
        if 'duckduckgo.com' in href: continue
        results.append({'title': title, 'body': snippet, 'href': href})
    return results

def auto_search(sid, keywords, region):
    """Search web and store results."""
    import sys, traceback
    db = None
    try:
        if USE_PG:
            db = psycopg2.connect(DATABASE_URL, sslmode='require')
            db.cursor_factory = psycopg2.extras.RealDictCursor
        else:
            DB_PATH = BASE / 'data' / 'crm.db'
            os.makedirs(BASE / 'data', exist_ok=True)
            db = sqlite3.connect(str(DB_PATH))

        def tq(sql, params=()):
            if USE_PG:
                sql = sql.replace('?', '%s'); c = db.cursor(); c.execute(sql, params); db.commit(); return c
            else:
                return db.execute(sql, params)
        def tq1(sql, params=()): return tq(sql, params).fetchone()
        def tqi(sql, params=()):
            sql = sql.replace(RETURNING, '')
            if USE_PG:
                sql = sql.replace('?','%s').replace(RETURNING,'') + ' RETURNING id'
                c = db.cursor(); c.execute(sql, params); db.commit(); return c.fetchone()['id']
            else:
                db.execute(sql, params); db.commit()
                return db.execute("SELECT last_insert_rowid()").fetchone()[0]

        all_leads, seen, loc = [], set(), region or ''
        for r in _web_search(f'{keywords} email contact', 8):
            href, body, title = r['href'], r['body'], r['title']
            domain = (re.findall(r'https?://(?:www\.)?([^/]+)', href) or [''])[0]
            if not domain or domain in seen: continue
            seen.add(domain)
            email = extract_email(body) or extract_email(title)
            company = title.split(' - ')[0].split(' | ')[0][:80]
            all_leads.append({'company':company,'email':email,'phone':extract_phone(body),'country':loc,'region':loc,'website':domain,'source':'DuckDuckGo','notes':body[:200]})

        sys.stderr.write(f'[Search #{sid}] {len(all_leads)} leads\n')
        for lead in all_leads:
            lead.setdefault('contact_name',''); lead.setdefault('title',''); lead.setdefault('address','')
            if lead.get('email') and tq1("SELECT id FROM customers WHERE email=?",(lead['email'],)): continue
            tqi(f"INSERT INTO search_leads (search_id,company,email,contact_name,title,phone,address,country,region,website,source,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?){RETURNING}",
                (sid,lead['company'],lead.get('email',''),lead['contact_name'],lead['title'],lead['phone'],lead['address'],lead['country'],lead['region'],lead['website'],lead['source'],lead['notes']))
        for lead in all_leads:
            if lead.get('email') and '@' in lead['email']:
                try:
                    v,d = smtp_probe(lead['email'])
                    tq("UPDATE search_leads SET email_validated=?, validation_detail=? WHERE search_id=? AND email=?", ('Yes' if v else ('No' if v is False else 'Unknown'), d, sid, lead['email']))
                except: pass
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        tq(f"UPDATE search_requests SET status='completed', processed_at='{now}' WHERE id=?",(sid,))
    except Exception as e:
        sys.stderr.write(f'[Search #{sid}] FAILED: {traceback.format_exc()}\n')
        try:
            err_now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            tq(f"UPDATE search_requests SET status='error', processed_at='{err_now}' WHERE id=?",(sid,))
        except: pass
    finally:
        if db: db.close()



@login_required
@app.route('/api/ai-search', methods=['POST'])
def ai_search():
    d = request.json; kw = d.get('keywords','').strip(); reg = d.get('region','').strip()
    if not kw: return jsonify({'error':'Keywords required'}), 400
    sid = qi(f"INSERT INTO search_requests (keywords,region) VALUES (?,?){RETURNING}", (kw,reg))
    threading.Thread(target=auto_search, args=(sid,kw,reg), daemon=True).start()
    return jsonify({'ok':True,'search_id':sid,'status':'searching'})


@login_required
@app.route('/api/ai-search/status')
def ai_search_status():
    since = (datetime.now() - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
    rows = qr("SELECT * FROM search_requests WHERE created_at >= ? ORDER BY created_at DESC LIMIT 20", (since,))
    result = []
    for r in rows:
        leads = qr("SELECT * FROM search_leads WHERE search_id=?",(r['id'],))
        result.append({'search_id':r['id'],'keywords':r['keywords'],'region':r['region'],'status':r['status'],'created_at':r['created_at'],'leads':[dict(l) for l in leads]})
    return jsonify(result)


@login_required
@app.route('/api/ai-search/results', methods=['POST'])
def ai_search_post_results():
    d = request.json; sid = d.get('search_id'); leads = d.get('leads',[])
    for lead in leads:
        q("INSERT INTO search_leads (search_id,company,email,contact_name,title,phone,address,country,region,website,source,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
          (sid,lead.get('company',''),lead.get('email',''),lead.get('contact_name',''),lead.get('title',''),lead.get('phone',''),lead.get('address',''),lead.get('country',''),lead.get('region',''),lead.get('website',''),lead.get('source','web_search'),lead.get('notes','')))
    for lead in leads:
        email = lead.get('email','').strip()
        if email and '@' in email:
            v,d = smtp_probe(email); q("UPDATE search_leads SET email_validated=?, validation_detail=? WHERE search_id=? AND email=?", ('Yes' if v else ('No' if v is False else 'Unknown'), d, sid, email))
    q(f"UPDATE search_requests SET status='completed', processed_at={NOW_SQL} WHERE id=?",(sid,))
    return jsonify({'ok':True,'leads_stored':len(leads)})


@login_required
@app.route('/api/ai-search/add-to-crm', methods=['POST'])
def ai_search_add_to_crm():
    ids = request.json.get('lead_ids',[]); added = 0
    for lid in ids:
        lead = q1("SELECT * FROM search_leads WHERE id=?",(lid,))
        if not lead or lead['added_to_crm']: continue
        email = (lead['email'] or '').strip()
        if email and q1("SELECT id FROM customers WHERE email=?",(email,)): continue
        q("INSERT INTO customers (company,email,contact_name,title,phone,address,country,region,website,source,grade,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
          (lead['company'],email,lead['contact_name'] or '',lead['title'] or '',lead['phone'] or '',lead['address'] or '',lead['country'] or '',lead['region'] or '',lead['website'] or '',lead['source'] or 'web_search','C',lead['notes'] or ''))
        q("UPDATE search_leads SET added_to_crm=1 WHERE id=?",(lid,)); added += 1
    return jsonify({'ok':True,'added':added})


@login_required
@app.route('/api/import', methods=['POST'])
def import_data():
    """Bulk import customers from JSON."""
    data = request.json
    customers = data.get('customers', [])
    count = 0
    for c in customers:
        email = (c.get('email') or '').strip()
        company = (c.get('company') or '').strip()
        if email and q1("SELECT id FROM customers WHERE email=?", (email,)): continue
        if company and q1("SELECT id FROM customers WHERE company=?", (company,)): continue
        fields = ['company','email','contact_name','title','phone','address','country','region','website','source','channel','grade','notes','company_bio','main_products','product_fit','email_department','customer_base','scale','founded','status','email_sent_date','email_validated','reply_received','reply_summary']
        vals = [c.get(f, '') for f in fields]
        ph = ','.join(['?'] * len(fields))
        q(f"INSERT INTO customers ({','.join(fields)}) VALUES ({ph})", vals)
        count += 1
    return jsonify({'ok': True, 'imported': count})

# ------- Admin -------
@app.route('/api/admin/users')
@admin_required
def admin_users():
    return jsonify([dict(r) for r in qr("SELECT id,username,role,created_at FROM users ORDER BY created_at")])

@app.route('/api/admin/users', methods=['POST'])
@admin_required
def admin_add_user():
    d = request.json
    username = (d.get('username') or '').strip()
    password = d.get('password', '')
    role = d.get('role', 'user')
    if not username or not password:
        return jsonify({'error':'Username and password required'}), 400
    if q1("SELECT id FROM users WHERE username=?",(username,)):
        return jsonify({'error':'Username exists'}), 409
    q("INSERT INTO users (username,password_hash,role) VALUES (?,?,?)",(username,generate_password_hash(password),role))
    return jsonify({'ok':True})

@app.route('/api/admin/users/<int:id>', methods=['PUT'])
@admin_required
def admin_update_user(id):
    d = request.json
    if d.get('password'):
        q("UPDATE users SET password_hash=?, role=? WHERE id=?",(generate_password_hash(d['password']),d.get('role','user'),id))
    else:
        q("UPDATE users SET role=? WHERE id=?",(d.get('role','user'),id))
    return jsonify({'ok':True})

@app.route('/api/admin/users/<int:id>', methods=['DELETE'])
@admin_required
def admin_delete_user(id):
    if id == session['user_id']: return jsonify({'error':'Cannot delete yourself'}), 400
    q("DELETE FROM users WHERE id=?",(id,))
    return jsonify({'ok':True})

# ------- Main -------
if __name__ == '__main__':
    with app.app_context():
        init_db()
        seed_templates()
        migrate_csv()
        if not q1("SELECT COUNT(*) as cnt FROM tasks") or q1("SELECT COUNT(*) as cnt FROM tasks")['cnt'] == 0:
            rows = qr("SELECT * FROM customers WHERE email_sent_date != '' AND is_blacklisted=0")
            for row in rows:
                dt = datetime.strptime(str(row['email_sent_date'])[:10],'%Y-%m-%d')
                for d,ttl in [(3,'Day 3 Light Touch'),(7,'Day 7 Value Add'),(14,'Day 14 Last Attempt'),(30,'Day 30 Closing Loop')]:
                    due = (dt+timedelta(days=d)).strftime('%Y-%m-%d')
                    q("INSERT INTO tasks (customer_id,title,due_date,type) VALUES (?,?,?,?)",(row['id'],ttl,due,'follow_up'))
    port = int(os.environ.get('PORT',5000))
    app.run(host='0.0.0.0' if IS_RENDER else '127.0.0.1', port=port, debug=False)
