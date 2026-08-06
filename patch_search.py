#!/usr/bin/env python3
with open(r"C:\Users\roy\OneDrive - 青島大藍門食品有限公司\sales\web_app\app.py", 'r', encoding='utf-8') as f:
    content = f.read()

old_marker = "def auto_search(sid, keywords, region):"
new_func = """def auto_search(sid, keywords, region):
    \"\"\"Multi-engine search: DuckDuckGo + LinkedIn + Business Directories.\"\"\"
    try:
        from ddgs import DDGS
        all_leads, seen_domains, loc = [], set(), region or ''

        # Strategy 1: DuckDuckGo general web
        try:
            for r in DDGS().text(f'{keywords} email contact', max_results=5):
                body, title, href = r.get('body',''), r.get('title',''), r.get('href','')
                domain = (re.findall(r'https?://(?:www\.)?([^/]+)', href) or [''])[0]
                if domain in seen_domains: continue
                seen_domains.add(domain)
                email = extract_email(body) or extract_email(title)
                company = title.split(' - ')[0].split(' | ')[0].split(' \\u2013 ')[0][:80]
                all_leads.append({'company':company,'email':email,'phone':extract_phone(body),'country':loc,'region':loc,'website':domain,'source':'DuckDuckGo','notes':body[:200]})
        except: pass

        # Strategy 2: LinkedIn company pages
        try:
            for r in DDGS().text(f'site:linkedin.com/company {keywords}', max_results=3):
                body, title, href = r.get('body',''), r.get('title',''), r.get('href','')
                company_name = title.replace('| LinkedIn','').replace('LinkedIn','').split(' - ')[0].strip()[:80] or title[:80]
                all_leads.append({'company':company_name,'email':'','phone':'','country':loc,'region':loc,'website':href,'source':'LinkedIn','notes':body[:200]})
        except: pass

        # Strategy 3: Business directories
        try:
            for r in DDGS().text(f'{keywords} yellowpages OR thomasnet OR kompass OR tradekey', max_results=3):
                body, title, href = r.get('body',''), r.get('title',''), r.get('href','')
                domain = (re.findall(r'https?://(?:www\.)?([^/]+)', href) or [''])[0]
                if domain in seen_domains: continue
                seen_domains.add(domain)
                email = extract_email(body) or extract_email(title)
                company = title.split(' - ')[0].split(' | ')[0][:80]
                src = 'YellowPages' if 'yellowpages' in domain else ('ThomasNet' if 'thomasnet' in domain else ('Kompass' if 'kompass' in domain else 'Business Directory'))
                all_leads.append({'company':company,'email':email,'phone':extract_phone(body),'country':loc,'region':loc,'website':domain,'source':src,'notes':body[:200]})
        except: pass

        # Strategy 4: Import/export directories
        try:
            for r in DDGS().text(f'{keywords} import export distributor wholesale', max_results=3):
                body, title, href = r.get('body',''), r.get('title',''), r.get('href','')
                domain = (re.findall(r'https?://(?:www\.)?([^/]+)', href) or [''])[0]
                if domain in seen_domains: continue
                seen_domains.add(domain)
                email = extract_email(body) or extract_email(title)
                company = title.split(' - ')[0].split(' | ')[0][:80]
                all_leads.append({'company':company,'email':email,'phone':extract_phone(body),'country':loc,'region':loc,'website':domain,'source':'Google Directory','notes':body[:200]})
        except: pass

        # Store + validate
        for lead in all_leads:
            lead.setdefault('contact_name',''); lead.setdefault('title',''); lead.setdefault('address','')
            if lead.get('email') and q1("SELECT id FROM customers WHERE email=?",(lead['email'],)): continue
            qi(f"INSERT INTO search_leads (search_id,company,email,contact_name,title,phone,address,country,region,website,source,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?){RETURNING}",
               (sid,lead['company'],lead.get('email',''),lead['contact_name'],lead['title'],lead['phone'],lead['address'],lead['country'],lead['region'],lead['website'],lead['source'],lead['notes']))
        for lead in all_leads:
            if lead.get('email') and '@' in lead['email']:
                try:
                    v,d = smtp_probe(lead['email'])
                    q("UPDATE search_leads SET email_validated=?, validation_detail=? WHERE search_id=? AND email=?", ('Yes' if v else ('No' if v is False else 'Unknown'), d, sid, lead['email']))
                except: pass
        q(f"UPDATE search_requests SET status='completed', processed_at={NOW_SQL} WHERE id=?",(sid,))
    except: q(f"UPDATE search_requests SET status='error', processed_at={NOW_SQL} WHERE id=?",(sid,))
"""

idx = content.find(old_marker)
next_def = content.find("\n@app.route('/api/ai-search'", idx)
content = content[:idx] + new_func + '\n' + content[next_def:]
with open(r"C:\Users\roy\OneDrive - 青島大藍門食品有限公司\sales\web_app\app.py", 'w', encoding='utf-8') as f:
    f.write(content)
print("OK")
