#!/usr/bin/env python3
"""Boxtray Sales System - Flask Backend (SQLite local / PostgreSQL cloud)"""
import os, csv, json, re, smtplib, dns.resolver, socket, threading
from datetime import datetime, timedelta
from pathlib import Path
from email.mime.text import MIMEText
from email.utils import formatdate
from flask import Flask, request, jsonify, g
from flask_cors import CORS

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
        return cur.fetchone()[0]
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
    if USE_PG: db.commit()

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

# ------- Routes -------
@app.route('/')
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

@app.route('/api/customers/<int:id>')
def get_customer(id):
    row = q1("SELECT * FROM customers WHERE id=?", (id,))
    if not row: return jsonify({'error':'Not found'}), 404
    return jsonify({'customer':dict(row), 'emails':[dict(e) for e in qr("SELECT * FROM emails WHERE customer_id=? ORDER BY sent_at DESC",(id,))],
        'tasks':[dict(t) for t in qr("SELECT * FROM tasks WHERE customer_id=? ORDER BY due_date ASC",(id,))]})

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

@app.route('/api/customers/<int:id>', methods=['DELETE'])
def delete_customer(id):
    q("UPDATE customers SET is_blacklisted=1, blacklist_reason='deleted' WHERE id=?",(id,))
    return jsonify({'ok':True})

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

@app.route('/api/validate-email', methods=['POST'])
def validate_email():
    email = request.json.get('email','').strip()
    if not email: return jsonify({'valid':False,'detail':'empty'})
    v,d = smtp_probe(email); return jsonify({'valid':v,'detail':d})

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

@app.route('/api/tasks', methods=['GET'])
def list_tasks():
    return jsonify([dict(r) for r in qr("SELECT t.*, c.company FROM tasks t LEFT JOIN customers c ON t.customer_id=c.id ORDER BY t.due_date ASC")])

@app.route('/api/tasks', methods=['POST'])
def add_task():
    d = request.json
    q("INSERT INTO tasks (customer_id,title,due_date,type,notes) VALUES (?,?,?,?,?)", (d.get('customer_id'),d['title'],d.get('due_date'),d.get('type','follow_up'),d.get('notes','')))
    return jsonify({'ok':True})

@app.route('/api/tasks/<int:id>', methods=['PUT'])
def update_task(id):
    d = request.json
    q("UPDATE tasks SET status=?, notes=? WHERE id=?", (d.get('status','pending'),d.get('notes',''),id))
    return jsonify({'ok':True})

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

@app.route('/api/templates')
def list_templates():
    tmpls = []
    td = (BASE / 'email-templates') if (BASE / 'email-templates').exists() else (SALES_DIR / '03-email-templates')
    for folder in ['01-cold-outreach','02-follow-up','03-reply-handling']:
        fp = td / folder
        if fp.exists():
            for f in sorted(fp.glob('*.md')):
                c = f.read_text(encoding='utf-8'); subj = ''
                for line in c.split('\n'):
                    if line.startswith('Subject:'): subj = line[8:].strip(); break
                tmpls.append({'name':f'{folder}/{f.stem}','subject':subj,'body':c})
    return jsonify(tmpls)

@app.route('/api/templates/render', methods=['POST'])
def render_template():
    d = request.json; cid = d.get('customer_id'); tp = d.get('template','')
    row = q1("SELECT * FROM customers WHERE id=?",(cid,))
    if not row: return jsonify({'error':'Not found'}), 404
    name = (row['contact_name'] or '').split()[0] if row['contact_name'] else 'there'
    td = (BASE / 'email-templates') if (BASE / 'email-templates').exists() else (SALES_DIR / '03-email-templates')
    parts = tp.split('/')
    fp = td / parts[0] / (parts[1]+'.md') if len(parts)==2 else td / tp
    if not fp.exists(): return jsonify({'error':'Template not found'}), 404
    content = fp.read_text(encoding='utf-8').replace('[First Name]',name)
    subj = ''; blines = []; found = False
    for line in content.split('\n'):
        if line.startswith('Subject:'): subj = line[8:].strip(); found = True
        elif found: blines.append(line)
    if not found: blines = content.split('\n')
    return jsonify({'subject':subj,'body':'\n'.join(blines).strip()})

@app.route('/api/channels', methods=['GET'])
def list_channels():
    return jsonify([dict(r) for r in qr("SELECT COALESCE(NULLIF(channel,''), source) as name, COUNT(*) as count FROM customers WHERE is_blacklisted=0 GROUP BY name")])

@app.route('/api/duplicates', methods=['GET'])
def check_duplicates():
    if USE_PG:
        ed = qr("SELECT email, COUNT(*) as cnt, STRING_AGG(id::text,',') as ids FROM customers WHERE email != '' AND is_blacklisted=0 GROUP BY email HAVING COUNT(*)>1")
        cd = qr("SELECT company, COUNT(*) as cnt, STRING_AGG(id::text,',') as ids FROM customers WHERE is_blacklisted=0 GROUP BY company HAVING COUNT(*)>1")
    else:
        ed = qr("SELECT email, COUNT(*) as cnt, GROUP_CONCAT(id) as ids FROM customers WHERE email != '' AND is_blacklisted=0 GROUP BY email HAVING cnt>1")
        cd = qr("SELECT company, COUNT(*) as cnt, GROUP_CONCAT(id) as ids FROM customers WHERE is_blacklisted=0 GROUP BY company HAVING cnt>1")
    return jsonify({'email_dupes':[{'email':r['email'],'ids':r['ids']} for r in ed],'company_dupes':[{'company':r['company'],'ids':r['ids']} for r in cd]})

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

def auto_search(sid, keywords, region):
    try:
        from ddgs import DDGS
        query = f'{keywords} email contact {region}'
        results = list(DDGS().text(query, max_results=8))
        leads = []; seen = set()
        for r in results:
            body = r.get('body',''); title = r.get('title',''); href = r.get('href','')
            domain = re.findall(r'https?://(?:www\.)?([^/]+)', href)
            domain = domain[0] if domain else ''
            if domain in seen: continue
            seen.add(domain)
            email = extract_email(body) or extract_email(title)
            company = title.split(' - ')[0].split(' | ')[0].split(' \u2013 ')[0][:80]
            leads.append({'company':company,'email':email,'contact_name':'','title':'','phone':extract_phone(body),'address':'','country':region or '','region':region or '','website':domain,'source':f'DuckDuckGo -> {domain}','notes':body[:200]})
        for lead in leads:
            if lead['email'] and q1("SELECT id FROM customers WHERE email=?",(lead['email'],)): continue
            lid = qi(f"INSERT INTO search_leads (search_id,company,email,contact_name,title,phone,address,country,region,website,source,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?){RETURNING}", (sid,lead['company'],lead['email'],lead['contact_name'],lead['title'],lead['phone'],lead['address'],lead['country'],lead['region'],lead['website'],lead['source'],lead['notes']))
        for lead in leads:
            if lead['email'] and '@' in lead['email']:
                try:
                    v,d = smtp_probe(lead['email'])
                    q("UPDATE search_leads SET email_validated=?, validation_detail=? WHERE search_id=? AND email=?", ('Yes' if v else ('No' if v is False else 'Unknown'), d, sid, lead['email']))
                except: pass
        q(f"UPDATE search_requests SET status='completed', processed_at={NOW_SQL} WHERE id=?",(sid,))
    except: q(f"UPDATE search_requests SET status='error', processed_at={NOW_SQL} WHERE id=?",(sid,))

@app.route('/api/ai-search', methods=['POST'])
def ai_search():
    d = request.json; kw = d.get('keywords','').strip(); reg = d.get('region','').strip()
    if not kw: return jsonify({'error':'Keywords required'}), 400
    sid = qi(f"INSERT INTO search_requests (keywords,region) VALUES (?,?){RETURNING}", (kw,reg))
    threading.Thread(target=auto_search, args=(sid,kw,reg), daemon=True).start()
    return jsonify({'ok':True,'search_id':sid,'status':'searching'})

@app.route('/api/ai-search/status')
def ai_search_status():
    rows = qr("SELECT * FROM search_requests WHERE created_at >= datetime('now','-24 hours') ORDER BY created_at DESC LIMIT 20")
    result = []
    for r in rows:
        leads = qr("SELECT * FROM search_leads WHERE search_id=?",(r['id'],))
        result.append({'search_id':r['id'],'keywords':r['keywords'],'region':r['region'],'status':r['status'],'created_at':r['created_at'],'leads':[dict(l) for l in leads]})
    return jsonify(result)

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

# ------- Main -------
if __name__ == '__main__':
    with app.app_context():
        init_db()
        migrate_csv()
        if not q1("SELECT COUNT(*) as cnt FROM tasks") or q1("SELECT COUNT(*) as cnt FROM tasks")['cnt'] == 0:
            rows = qr("SELECT * FROM customers WHERE email_sent_date != '' AND is_blacklisted=0")
            for row in rows:
                dt = datetime.strptime(str(row['email_sent_date'])[:10],'%Y-%m-%d')
                for d,ttl in [(3,'Day 3 Light Touch'),(7,'Day 7 Value Add'),(14,'Day 14 Last Attempt'),(30,'Day 30 Closing Loop')]:
                    due = (dt+timedelta(days=d)).strftime('%Y-%m-%d')
                    q("INSERT INTO tasks (customer_id,title,due_date,type) VALUES (?,?,?,?)",(row['id'],ttl,due,'follow_up'))
    port = int(os.environ.get('PORT',5000))
    app.run(host='0.0.0.0' if IS_RENDER else '127.0.0.1', port=port, debug=not IS_RENDER)
