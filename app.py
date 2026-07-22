from flask import Flask, request, redirect, url_for, session, flash, send_file, jsonify, render_template_string
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from pathlib import Path
from functools import wraps
from datetime import date, datetime, timedelta
import sqlite3, os, secrets, math

app=Flask(__name__); app.secret_key=os.getenv('SECRET_KEY','dev-'+secrets.token_hex(16))
BASE=Path(__file__).parent; VOL=Path(os.getenv('RAILWAY_VOLUME_MOUNT_PATH',BASE)); DB=Path(os.getenv('DB_PATH',VOL/'titan.db')); UP=Path(os.getenv('UPLOAD_PATH',VOL/'uploads')); UP.mkdir(parents=True,exist_ok=True); DB.parent.mkdir(parents=True,exist_ok=True)
def db():
 c=sqlite3.connect(DB,timeout=30); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); c.execute('PRAGMA journal_mode=WAL'); return c
with db() as d:
 d.executescript('''
 CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT,email TEXT UNIQUE,password TEXT);
 CREATE TABLE IF NOT EXISTS profiles(user_id INTEGER PRIMARY KEY,age REAL DEFAULT 27,height REAL DEFAULT 1.8,start_weight REAL DEFAULT 65,goal_weight REAL DEFAULT 75,final_goal REAL DEFAULT 85,calories REAL DEFAULT 2800,protein REAL DEFAULT 130,carbs REAL DEFAULT 380,fat REAL DEFAULT 85,water REAL DEFAULT 2.5);
 CREATE TABLE IF NOT EXISTS weights(id INTEGER PRIMARY KEY,user_id INTEGER,day TEXT,weight REAL,UNIQUE(user_id,day));
 CREATE TABLE IF NOT EXISTS measurements(id INTEGER PRIMARY KEY,user_id INTEGER,day TEXT,arm REAL,chest REAL,waist REAL,abdomen REAL,thigh REAL,calf REAL,shoulders REAL);
 CREATE TABLE IF NOT EXISTS photos(id INTEGER PRIMARY KEY,user_id INTEGER,day TEXT,angle TEXT,filename TEXT,note TEXT);
 CREATE TABLE IF NOT EXISTS foods(id INTEGER PRIMARY KEY,user_id INTEGER,name TEXT,serving REAL,calories REAL,protein REAL,carbs REAL,fat REAL);
 CREATE TABLE IF NOT EXISTS meals(id INTEGER PRIMARY KEY,user_id INTEGER,day TEXT,meal_name TEXT,food_id INTEGER,quantity REAL);
 CREATE TABLE IF NOT EXISTS workouts(id INTEGER PRIMARY KEY,user_id INTEGER,day TEXT,exercise TEXT,sets INTEGER,reps INTEGER,load REAL,notes TEXT);
 CREATE TABLE IF NOT EXISTS exercises(id INTEGER PRIMARY KEY,user_id INTEGER,name TEXT,muscle TEXT,description TEXT,image_url TEXT,video_url TEXT);
 CREATE TABLE IF NOT EXISTS habits(user_id INTEGER,day TEXT,water REAL,sleep REAL,trained INTEGER,PRIMARY KEY(user_id,day));
 CREATE TABLE IF NOT EXISTS budget(id INTEGER PRIMARY KEY,user_id INTEGER,name TEXT,unit TEXT,daily_qty REAL,package_qty REAL,price REAL,stock REAL DEFAULT 0);
 CREATE TABLE IF NOT EXISTS stores(id INTEGER PRIMARY KEY,user_id INTEGER,name TEXT);
 CREATE TABLE IF NOT EXISTS prices(id INTEGER PRIMARY KEY,user_id INTEGER,store_id INTEGER,item TEXT,qty REAL,unit TEXT,price REAL);
 CREATE TABLE IF NOT EXISTS calendar(id INTEGER PRIMARY KEY,user_id INTEGER,day TEXT,time TEXT,title TEXT,description TEXT);
 CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY,user_id INTEGER,title TEXT,time TEXT,days TEXT,active INTEGER DEFAULT 1);
 ''')
def req(f):
 @wraps(f)
 def w(*a,**k): return f(*a,**k) if session.get('uid') else redirect(url_for('login'))
 return w
def uid(): return session['uid']
def td(): return date.today().isoformat()
def seed(u):
 with db() as d:
  d.execute('INSERT OR IGNORE INTO profiles(user_id) VALUES(?)',(u,))
  if not d.execute('SELECT 1 FROM foods WHERE user_id=?',(u,)).fetchone(): d.executemany('INSERT INTO foods(user_id,name,serving,calories,protein,carbs,fat) VALUES(?,?,?,?,?,?,?)',[(u,'Arroz cozido',100,130,2.7,28,.3),(u,'Feijão cozido',100,76,4.8,13.6,.5),(u,'Peito de frango',100,165,31,0,3.6),(u,'Ovo inteiro',50,72,6.3,.4,4.8),(u,'Leite integral',200,122,6.4,9.4,6.6),(u,'Banana',100,89,1.1,23,.3),(u,'Aveia',40,152,5.1,27,2.8)])
  if not d.execute('SELECT 1 FROM exercises WHERE user_id=?',(u,)).fetchone(): d.executemany('INSERT INTO exercises(user_id,name,muscle,description,image_url,video_url) VALUES(?,?,?,?,?,?)',[(u,'Agachamento','Pernas','Mantenha o tronco firme e os joelhos alinhados.','',''),(u,'Supino reto','Peito','Controle a descida e mantenha as escápulas apoiadas.','',''),(u,'Remada baixa','Costas','Puxe pelos cotovelos sem balançar o tronco.','','')])
  if not d.execute('SELECT 1 FROM budget WHERE user_id=?',(u,)).fetchone(): d.executemany('INSERT INTO budget(user_id,name,unit,daily_qty,package_qty,price,stock) VALUES(?,?,?,?,?,?,?)',[(u,'Arroz cru','g',180,5000,32.9,0),(u,'Feijão cru','g',100,1000,9.99,0),(u,'Frango cru','g',450,1000,21.9,0),(u,'Leite integral','ml',1000,1000,5.8,0)])
  d.commit()
BASEHTML='''<!doctype html><html lang=pt-BR><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>TITAN V3</title><link rel=stylesheet href="/static/style.css"></head><body><header><a class=brand href="/">TITAN <b>V3</b><small>EVOLUÇÃO FÍSICA</small></a>{% if session.uid %}<button class=toggle onclick="document.querySelector('nav').classList.toggle('open')">☰</button><nav><a href="/">Painel</a><a href="/nutrition">Nutrição</a><a href="/progress">Evolução</a><a href="/training">Treinos</a><a href="/finance">Financeiro</a><a href="/calendar">Calendário</a><a href="/settings">Metas</a><a href="/backup">Backup</a><a href="/logout">Sair</a></nav>{% endif %}</header><main>{% for m in get_flashed_messages() %}<div class=flash>{{m}}</div>{% endfor %}CONTENT</main><footer>TITAN V3 • acompanhamento pessoal</footer></body></html>'''
def page(content,**ctx): return render_template_string(BASEHTML.replace('CONTENT',content),**ctx)
@app.get('/health')
def health(): return jsonify(status='ok')
@app.route('/register',methods=['GET','POST'])
def register():
 if request.method=='POST':
  try:
   with db() as d: c=d.execute('INSERT INTO users(name,email,password) VALUES(?,?,?)',(request.form['name'],request.form['email'].lower(),generate_password_hash(request.form['password']))); d.commit(); seed(c.lastrowid)
   flash('Conta criada.'); return redirect('/login')
  except sqlite3.IntegrityError: flash('E-mail já cadastrado.')
 return page('''<section class="panel auth"><h1>Criar conta</h1><form method=post class=form><label>Nome<input name=name required></label><label>E-mail<input type=email name=email required></label><label>Senha<input type=password minlength=6 name=password required></label><button>Cadastrar</button></form><p><a href=/login>Já tenho conta</a></p></section>''')
@app.route('/login',methods=['GET','POST'])
def login():
 if request.method=='POST':
  with db() as d: u=d.execute('SELECT * FROM users WHERE email=?',(request.form['email'].lower(),)).fetchone()
  if u and check_password_hash(u['password'],request.form['password']): session.update(uid=u['id'],name=u['name']); return redirect('/')
  flash('Dados inválidos.')
 return page('''<section class="panel auth"><h1>Entrar no TITAN</h1><form method=post class=form><label>E-mail<input type=email name=email required></label><label>Senha<input type=password name=password required></label><button>Entrar</button></form><p><a href=/register>Criar conta</a></p></section>''')
@app.get('/logout')
def logout(): session.clear(); return redirect('/login')
@app.get('/uploads/<path:n>')
@req
def uploads(n): return send_file(UP/n)

def prediction(ws):
 if len(ws)<2:return []
 first,last=ws[0],ws[-1]; days=max(1,(datetime.fromisoformat(last['day'])-datetime.fromisoformat(first['day'])).days); rate=(last['weight']-first['weight'])/days
 return [(t,'atingido' if last['weight']>=t else (date.today()+timedelta(days=math.ceil((t-last['weight'])/rate))).strftime('%d/%m/%Y') if rate>0 else 'sem previsão') for t in [70,75,80,85]]
@app.route('/',methods=['GET','POST'])
@req
def dashboard():
 if request.method=='POST':
  with db() as d:
   if request.form['kind']=='weight': d.execute('INSERT INTO weights(user_id,day,weight) VALUES(?,?,?) ON CONFLICT(user_id,day) DO UPDATE SET weight=excluded.weight',(uid(),request.form['day'],float(request.form['weight'])))
   else:d.execute('INSERT INTO habits(user_id,day,water,sleep,trained) VALUES(?,?,?,?,?) ON CONFLICT(user_id,day) DO UPDATE SET water=excluded.water,sleep=excluded.sleep,trained=excluded.trained',(uid(),request.form['day'],float(request.form.get('water') or 0),float(request.form.get('sleep') or 0),1 if request.form.get('trained') else 0))
   d.commit(); return redirect('/')
 with db() as d:
  p=d.execute('SELECT * FROM profiles WHERE user_id=?',(uid(),)).fetchone(); ws=d.execute('SELECT * FROM weights WHERE user_id=? ORDER BY day',(uid(),)).fetchall(); latest=ws[-1]['weight'] if ws else p['start_weight']; day=td(); h=d.execute('SELECT * FROM habits WHERE user_id=? AND day=?',(uid(),day)).fetchone(); t=d.execute('''SELECT COALESCE(SUM(f.calories*m.quantity/f.serving),0)cals,COALESCE(SUM(f.protein*m.quantity/f.serving),0)prot FROM meals m JOIN foods f ON f.id=m.food_id WHERE m.user_id=? AND m.day=?''',(uid(),day)).fetchone(); upcoming=d.execute('SELECT * FROM calendar WHERE user_id=? AND day>=? ORDER BY day,time LIMIT 5',(uid(),day)).fetchall()
 prog=max(0,min(100,(latest-p['start_weight'])/max(.1,p['goal_weight']-p['start_weight'])*100)); tips=[]
 tips.append(f'Hoje faltam cerca de {max(0,p["calories"]-t["cals"]):.0f} kcal.' if t['cals']<p['calories'] else 'Meta calórica alcançada hoje.')
 if h and h['sleep']<7: tips.append('Sono abaixo de 7 horas: priorize recuperação.')
 return page('''<div class=hero><div><small>OLÁ, {{session.name}}</small><h1>Dashboard profissional</h1><p>Visão integrada da sua evolução.</p></div><div class=ring><b>{{'%.1f'|format(latest)}} kg</b><span>{{'%.0f'|format(prog)}}% da meta</span></div></div><div class=stats><article>Calorias<strong>{{'%.0f'|format(t.cals)}} / {{p.calories}}</strong></article><article>Proteína<strong>{{'%.0f'|format(t.prot)}} / {{p.protein}} g</strong></article><article>Água<strong>{{h.water if h else 0}} / {{p.water}} L</strong></article><article>Meta<strong>{{p.goal_weight}} kg</strong></article></div><div class=two><section class=panel><h2>Registro rápido</h2><form method=post class=form><input type=hidden name=kind value=weight><input type=date name=day value={{day}}><input type=number step=.01 name=weight placeholder="Peso" required><button>Salvar peso</button></form><form method=post class=form><input type=hidden name=kind value=habit><input type=hidden name=day value={{day}}><div class=grid><input type=number step=.1 name=water placeholder="Água (L)"><input type=number step=.1 name=sleep placeholder="Sono (h)"></div><label><input type=checkbox name=trained> Treinei hoje</label><button>Salvar hábitos</button></form></section><section class=panel><h2>IA TITAN</h2>{% for x in tips %}<div class=insight>{{x}}</div>{% endfor %}</section></div><div class=two><section class=panel><h2>Previsão de 70, 75, 80 e 85 kg</h2>{% for a,b in preds %}<div class=row><b>{{a}} kg</b><span>{{b}}</span></div>{% else %}<p>Registre ao menos dois pesos.</p>{% endfor %}</section><section class=panel><h2>Próximas refeições</h2>{% for x in upcoming %}<div class=row><b>{{x.day}} {{x.time}}</b><span>{{x.title}}</span></div>{% endfor %}</section></div>''',p=p,latest=latest,prog=prog,t=t,h=h,day=day,tips=tips,preds=prediction(ws),upcoming=upcoming)
@app.route('/nutrition',methods=['GET','POST'])
@req
def nutrition():
 day=request.args.get('day',td())
 with db() as d:
  if request.method=='POST':
   if request.form['action']=='food': d.execute('INSERT INTO foods(user_id,name,serving,calories,protein,carbs,fat) VALUES(?,?,?,?,?,?,?)',(uid(),request.form['name'],*[float(request.form[x]) for x in ['serving','calories','protein','carbs','fat']]))
   else:d.execute('INSERT INTO meals(user_id,day,meal_name,food_id,quantity) VALUES(?,?,?,?,?)',(uid(),request.form['day'],request.form['meal_name'],request.form['food_id'],request.form['quantity']))
   d.commit(); return redirect('/nutrition')
  fs=d.execute('SELECT * FROM foods WHERE user_id=? ORDER BY name',(uid(),)).fetchall(); ms=d.execute('SELECT m.*,f.name FROM meals m JOIN foods f ON f.id=m.food_id WHERE m.user_id=? AND m.day=?',(uid(),day)).fetchall()
 return page('''<h1>Nutrição</h1><div class=two><section class=panel><h2>Registrar refeição</h2><form method=post class=form><input type=hidden name=action value=meal><input type=date name=day value={{day}}><input name=meal_name value=Almoço><select name=food_id>{% for f in fs %}<option value={{f.id}}>{{f.name}}</option>{% endfor %}</select><input type=number step=.1 name=quantity placeholder=Quantidade required><button>Adicionar</button></form></section><section class=panel><h2>Novo alimento</h2><form method=post class=form><input type=hidden name=action value=food><input name=name placeholder=Nome required><div class=grid>{% for x in ['serving','calories','protein','carbs','fat'] %}<input type=number step=.1 name={{x}} placeholder={{x}} required>{% endfor %}</div><button>Cadastrar</button></form></section></div><section class=panel><h2>Refeições</h2>{% for m in ms %}<div class=row><b>{{m.meal_name}} — {{m.name}}</b><span>{{m.quantity}} g</span></div>{% endfor %}</section>''',fs=fs,ms=ms,day=day)
@app.route('/progress',methods=['GET','POST'])
@req
def progress():
 with db() as d:
  if request.method=='POST':
   if request.form['action']=='measure': d.execute('INSERT INTO measurements(user_id,day,arm,chest,waist,abdomen,thigh,calf,shoulders) VALUES(?,?,?,?,?,?,?,?,?)',(uid(),request.form['day'],*[float(request.form.get(x) or 0) for x in ['arm','chest','waist','abdomen','thigh','calf','shoulders']]))
   else:
    f=request.files['photo']; n=f'{uid()}_{datetime.now():%Y%m%d%H%M%S}_{secure_filename(f.filename)}'; f.save(UP/n); d.execute('INSERT INTO photos(user_id,day,angle,filename,note) VALUES(?,?,?,?,?)',(uid(),request.form['day'],request.form['angle'],n,request.form.get('note','')))
   d.commit(); return redirect('/progress')
  ms=d.execute('SELECT * FROM measurements WHERE user_id=? ORDER BY day DESC',(uid(),)).fetchall(); ps=d.execute('SELECT * FROM photos WHERE user_id=? ORDER BY day DESC',(uid(),)).fetchall()
 return page('''<h1>Fotos e medidas</h1><div class=two><section class=panel><h2>Medidas corporais</h2><form method=post class=form><input type=hidden name=action value=measure><input type=date name=day value={{day}}><div class=grid>{% for x in ['arm','chest','waist','abdomen','thigh','calf','shoulders'] %}<input type=number step=.1 name={{x}} placeholder={{x}}>{% endfor %}</div><button>Salvar</button></form></section><section class=panel><h2>Foto de evolução</h2><form method=post enctype=multipart/form-data class=form><input type=hidden name=action value=photo><input type=date name=day value={{day}}><select name=angle><option>Frente</option><option>Lado</option><option>Costas</option></select><input type=file name=photo accept=image/* required><input name=note placeholder=Observação><button>Enviar</button></form></section></div><section class=panel><h2>Galeria</h2><div class=gallery>{% for p in ps %}<figure><img src="/uploads/{{p.filename}}"><figcaption>{{p.day}} • {{p.angle}}</figcaption></figure>{% endfor %}</div></section><section class=panel><h2>Histórico de medidas</h2>{% for m in ms %}<div class=row><b>{{m.day}}</b><span>Braço {{m.arm}} | Peito {{m.chest}} | Cintura {{m.waist}}</span></div>{% endfor %}</section>''',day=td(),ms=ms,ps=ps)
@app.route('/training',methods=['GET','POST'])
@req
def training():
 with db() as d:
  if request.method=='POST':
   if request.form['action']=='exercise':d.execute('INSERT INTO exercises(user_id,name,muscle,description,image_url,video_url) VALUES(?,?,?,?,?,?)',(uid(),request.form['name'],request.form['muscle'],request.form['description'],request.form.get('image_url',''),request.form.get('video_url','')))
   else:d.execute('INSERT INTO workouts(user_id,day,exercise,sets,reps,load,notes) VALUES(?,?,?,?,?,?,?)',(uid(),request.form['day'],request.form['exercise'],request.form['sets'],request.form['reps'],request.form['load'],request.form.get('notes','')))
   d.commit(); return redirect('/training')
  es=d.execute('SELECT * FROM exercises WHERE user_id=?',(uid(),)).fetchall(); ws=d.execute('SELECT * FROM workouts WHERE user_id=? ORDER BY day DESC',(uid(),)).fetchall()
 return page('''<h1>Treinos e exercícios</h1><div class=two><section class=panel><h2>Registrar treino</h2><form method=post class=form><input type=hidden name=action value=workout><input type=date name=day value={{day}}><select name=exercise>{% for e in es %}<option>{{e.name}}</option>{% endfor %}</select><div class=grid><input type=number name=sets value=3><input type=number name=reps value=10><input type=number step=.5 name=load placeholder=Carga></div><input name=notes placeholder=Notas><button>Salvar</button></form></section><section class=panel><h2>Novo exercício</h2><form method=post class=form><input type=hidden name=action value=exercise><input name=name placeholder=Nome required><input name=muscle placeholder="Grupo muscular"><textarea name=description placeholder=Descrição></textarea><input name=image_url placeholder="URL da imagem"><input name=video_url placeholder="URL do vídeo"><button>Cadastrar</button></form></section></div><section class=panel><h2>Banco de exercícios</h2><div class=gallery>{% for e in es %}<article><h3>{{e.name}}</h3><small>{{e.muscle}}</small><p>{{e.description}}</p>{% if e.image_url %}<img src={{e.image_url}}>{% endif %}{% if e.video_url %}<a href={{e.video_url}} target=_blank>Abrir vídeo</a>{% endif %}</article>{% endfor %}</div></section><section class=panel><h2>Histórico</h2>{% for w in ws %}<div class=row><b>{{w.day}} — {{w.exercise}}</b><span>{{w.sets}}x{{w.reps}} • {{w.load}} kg</span></div>{% endfor %}</section>''',es=es,ws=ws,day=td())
@app.route('/finance',methods=['GET','POST'])
@req
def finance():
 with db() as d:
  if request.method=='POST':
   a=request.form['action']
   if a=='budget':d.execute('INSERT INTO budget(user_id,name,unit,daily_qty,package_qty,price,stock) VALUES(?,?,?,?,?,?,?)',(uid(),request.form['name'],request.form['unit'],request.form['daily_qty'],request.form['package_qty'],request.form['price'],request.form.get('stock',0)))
   elif a=='store':d.execute('INSERT INTO stores(user_id,name) VALUES(?,?)',(uid(),request.form['name']))
   else:d.execute('INSERT INTO prices(user_id,store_id,item,qty,unit,price) VALUES(?,?,?,?,?,?)',(uid(),request.form['store_id'],request.form['item'],request.form['qty'],request.form['unit'],request.form['price']))
   d.commit(); return redirect('/finance')
  items=[dict(x) for x in d.execute('SELECT * FROM budget WHERE user_id=?',(uid(),)).fetchall()]; total=0
  for x in items:x['packs']=math.ceil(max(0,x['daily_qty']*30-x['stock'])/x['package_qty']);x['cost']=x['packs']*x['price'];total+=x['cost']
  stores=d.execute('SELECT * FROM stores WHERE user_id=?',(uid(),)).fetchall(); prices=d.execute('SELECT p.*,s.name store FROM prices p JOIN stores s ON s.id=p.store_id WHERE p.user_id=? ORDER BY item,price',(uid(),)).fetchall(); totals=d.execute('SELECT s.name,COALESCE(SUM(p.price),0) total FROM stores s LEFT JOIN prices p ON p.store_id=s.id WHERE s.user_id=? GROUP BY s.id ORDER BY total',(uid(),)).fetchall()
 return page('''<div class=hero><h1>Financeiro da dieta</h1><div class=ring><b>R$ {{'%.2f'|format(total)}}</b><span>por mês</span></div></div><div class=two><section class=panel><h2>Item mensal</h2><form method=post class=form><input type=hidden name=action value=budget><input name=name placeholder=Produto required><div class=grid><input name=unit value=g><input type=number step=.1 name=daily_qty placeholder="Consumo diário"><input type=number step=.1 name=package_qty placeholder=Embalagem><input type=number step=.01 name=price placeholder=Preço><input type=number step=.1 name=stock placeholder=Estoque></div><button>Adicionar</button></form></section><section class=panel><h2>Comparador de supermercados</h2><form method=post class=inline><input type=hidden name=action value=store><input name=name placeholder=Mercado><button>Adicionar</button></form><form method=post class=form><input type=hidden name=action value=price><select name=store_id>{% for s in stores %}<option value={{s.id}}>{{s.name}}</option>{% endfor %}</select><input name=item placeholder=Produto><div class=grid><input type=number step=.1 name=qty placeholder=Quantidade><input name=unit value=g><input type=number step=.01 name=price placeholder=Preço></div><button>Salvar preço</button></form></section></div><section class=panel><h2>Compra mensal</h2>{% for x in items %}<div class=row><b>{{x.name}} — {{x.packs}} pacote(s)</b><span>R$ {{'%.2f'|format(x.cost)}}</span></div>{% endfor %}</section><div class=two><section class=panel><h2>Ranking dos mercados</h2>{% for x in totals %}<div class=row><b>{{x.name}}</b><span>R$ {{'%.2f'|format(x.total)}}</span></div>{% endfor %}</section><section class=panel><h2>Preços</h2>{% for x in prices %}<div class=row><b>{{x.item}} • {{x.store}}</b><span>R$ {{x.price}}</span></div>{% endfor %}</section></div>''',items=items,total=total,stores=stores,prices=prices,totals=totals)
@app.route('/calendar',methods=['GET','POST'])
@req
def calendar():
 with db() as d:
  if request.method=='POST':
   if request.form['action']=='meal':d.execute('INSERT INTO calendar(user_id,day,time,title,description) VALUES(?,?,?,?,?)',(uid(),request.form['day'],request.form['time'],request.form['title'],request.form.get('description','')))
   else:d.execute('INSERT INTO reminders(user_id,title,time,days) VALUES(?,?,?,?)',(uid(),request.form['title'],request.form['time'],request.form.get('days','Todos os dias')))
   d.commit(); return redirect('/calendar')
  cs=d.execute('SELECT * FROM calendar WHERE user_id=? ORDER BY day,time',(uid(),)).fetchall(); rs=[dict(x) for x in d.execute('SELECT * FROM reminders WHERE user_id=?',(uid(),)).fetchall()]
 return page('''<div class=hero><h1>Calendário e alarmes</h1><button onclick="Notification.requestPermission()">Ativar notificações</button></div><div class=two><section class=panel><h2>Programar refeição</h2><form method=post class=form><input type=hidden name=action value=meal><input type=date name=day value={{day}}><input type=time name=time required><input name=title placeholder=Título required><textarea name=description placeholder=Descrição></textarea><button>Adicionar</button></form></section><section class=panel><h2>Alarme para comer</h2><form method=post class=form><input type=hidden name=action value=alarm><input name=title placeholder="Hora do lanche" required><input type=time name=time required><input name=days value="Todos os dias"><button>Criar alarme</button></form><p>As notificações exigem o site aberto.</p></section></div><section class=panel><h2>Agenda</h2>{% for x in cs %}<div class=row><b>{{x.day}} {{x.time}}</b><span>{{x.title}}</span></div>{% endfor %}</section><script>const A={{rs|tojson}};setInterval(()=>{let n=new Date(),h=String(n.getHours()).padStart(2,'0')+':'+String(n.getMinutes()).padStart(2,'0');A.forEach(a=>{let k=a.id+n.toDateString();if(a.time===h&&Notification.permission==='granted'&&!sessionStorage[k]){new Notification('TITAN',{body:a.title});sessionStorage[k]=1}})},30000)</script>''',day=td(),cs=cs,rs=rs)
@app.route('/settings',methods=['GET','POST'])
@req
def settings():
 with db() as d:
  if request.method=='POST': d.execute('UPDATE profiles SET age=?,height=?,start_weight=?,goal_weight=?,final_goal=?,calories=?,protein=?,carbs=?,fat=?,water=? WHERE user_id=?',(*[request.form[x] for x in ['age','height','start_weight','goal_weight','final_goal','calories','protein','carbs','fat','water']],uid())); d.commit(); flash('Metas salvas.'); return redirect('/settings')
  p=d.execute('SELECT * FROM profiles WHERE user_id=?',(uid(),)).fetchone()
 return page('''<section class="panel auth"><h1>Metas pessoais</h1><form method=post class=form><div class=grid>{% for x,l in fields %}<label>{{l}}<input type=number step=.01 name={{x}} value={{p[x]}}></label>{% endfor %}</div><button>Salvar</button></form></section>''',p=p,fields=[('age','Idade'),('height','Altura'),('start_weight','Peso inicial'),('goal_weight','Meta atual'),('final_goal','Meta final'),('calories','Calorias'),('protein','Proteína'),('carbs','Carboidratos'),('fat','Gorduras'),('water','Água')])
@app.get('/backup')
@req
def backup():
 out=DB.parent/'titan_backup.db'
 with sqlite3.connect(DB) as s,sqlite3.connect(out) as t:s.backup(t)
 return send_file(out,as_attachment=True)
if __name__=='__main__':app.run(host='0.0.0.0',port=int(os.getenv('PORT',5000)))
