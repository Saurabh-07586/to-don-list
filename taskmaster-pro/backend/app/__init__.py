import os,json,hashlib,secrets,smtplib,ssl
import csv,io
import calendar
import uuid,mimetypes
from datetime import datetime,timedelta,timezone
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote
from flask import Flask,request,jsonify,Response,send_file,abort
from sqlalchemy import String,cast,or_
from sqlalchemy.exc import SQLAlchemyError
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_jwt_extended import JWTManager,create_access_token,get_jwt_identity,jwt_required
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import generate_password_hash,check_password_hash
from werkzeug.utils import secure_filename
import requests
from dotenv import load_dotenv

load_dotenv()

db=SQLAlchemy(); migrate=Migrate(); jwt=JWTManager(); limiter=Limiter(key_func=get_remote_address,default_limits=['200/day','60/hour'])
PRIORITIES={'low','medium','high','urgent'}; STATUSES={'todo','in_progress','completed'}
def now(): return datetime.now(timezone.utc).replace(tzinfo=None)
def stamp(value):
    if not value:return None
    value=value.replace(tzinfo=timezone.utc)if value.tzinfo is None else value.astimezone(timezone.utc)
    return value.isoformat().replace('+00:00','Z')
def date(value):
    if not value:return None
    try:return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError,AttributeError):raise ValueError('Dates must be ISO-8601 values.')

class User(db.Model):
    id=db.Column(db.Integer,primary_key=True); name=db.Column(db.String(120),nullable=False); email=db.Column(db.String(255),unique=True,index=True,nullable=False); password_hash=db.Column(db.String(255),nullable=False); preferences=db.Column(db.JSON,default=dict); created_at=db.Column(db.DateTime(timezone=True),default=now)
project_tasks=db.Table('project_tasks',db.Column('project_id',db.Integer,db.ForeignKey('project.id',ondelete='CASCADE'),primary_key=True),db.Column('task_id',db.Integer,db.ForeignKey('task.id',ondelete='CASCADE'),primary_key=True))
class Project(db.Model):
    id=db.Column(db.Integer,primary_key=True);user_id=db.Column(db.Integer,db.ForeignKey('user.id'),nullable=False,index=True);name=db.Column(db.String(120),nullable=False);description=db.Column(db.String(2000),default='');created_at=db.Column(db.DateTime(timezone=True),default=now)
    tasks=db.relationship('Task',secondary=project_tasks,back_populates='projects');milestones=db.relationship('Milestone',cascade='all,delete-orphan',backref='project',lazy=True)
    __table_args__=(db.UniqueConstraint('user_id','name',name='uq_project_user_name'),)
class ProjectMember(db.Model):
    id=db.Column(db.Integer,primary_key=True);project_id=db.Column(db.Integer,db.ForeignKey('project.id'),nullable=False,index=True);user_id=db.Column(db.Integer,db.ForeignKey('user.id'),nullable=False,index=True);invited_by=db.Column(db.Integer,db.ForeignKey('user.id'),nullable=False);created_at=db.Column(db.DateTime(timezone=True),default=now,nullable=False)
    __table_args__=(db.UniqueConstraint('project_id','user_id',name='uq_project_member'),)
class ProjectComment(db.Model):
    id=db.Column(db.Integer,primary_key=True);project_id=db.Column(db.Integer,db.ForeignKey('project.id'),nullable=False,index=True);author_id=db.Column(db.Integer,db.ForeignKey('user.id'),nullable=False);message=db.Column(db.String(2000),nullable=False);created_at=db.Column(db.DateTime(timezone=True),default=now,nullable=False)
class TaskAssignment(db.Model):
    id=db.Column(db.Integer,primary_key=True);project_id=db.Column(db.Integer,db.ForeignKey('project.id'),nullable=False,index=True);task_id=db.Column(db.Integer,db.ForeignKey('task.id'),nullable=False,index=True);user_id=db.Column(db.Integer,db.ForeignKey('user.id'),nullable=False,index=True);assigned_by=db.Column(db.Integer,db.ForeignKey('user.id'),nullable=False);created_at=db.Column(db.DateTime(timezone=True),default=now,nullable=False)
    __table_args__=(db.UniqueConstraint('project_id','task_id','user_id',name='uq_project_task_assignee'),)
class Task(db.Model):
    id=db.Column(db.Integer,primary_key=True); user_id=db.Column(db.Integer,db.ForeignKey('user.id'),nullable=False,index=True); title=db.Column(db.String(200),nullable=False); description=db.Column(db.Text,default=''); status=db.Column(db.String(20),default='todo',index=True); priority=db.Column(db.String(20),default='medium',index=True); category=db.Column(db.String(80),default='General',index=True); tags=db.Column(db.JSON,default=list); start_date=db.Column(db.DateTime(timezone=True)); due_date=db.Column(db.DateTime(timezone=True),index=True); scheduled_start=db.Column(db.DateTime(timezone=True)); estimated_minutes=db.Column(db.Integer,default=30); actual_minutes=db.Column(db.Integer,default=0); created_at=db.Column(db.DateTime(timezone=True),default=now); updated_at=db.Column(db.DateTime(timezone=True),default=now,onupdate=now)
    subtasks=db.relationship('Subtask',cascade='all,delete-orphan',backref='task',lazy=True);attachments=db.relationship('Attachment',cascade='all,delete-orphan',backref='task',lazy=True); dependencies=db.relationship('Task',secondary='dependency',primaryjoin='Task.id == Dependency.task_id',secondaryjoin='Task.id == Dependency.depends_on_id',viewonly=True);projects=db.relationship('Project',secondary=project_tasks,back_populates='tasks');recurrence=db.relationship('TaskRecurrence',uselist=False,cascade='all,delete-orphan',backref='task')
class Subtask(db.Model):
    id=db.Column(db.Integer,primary_key=True); task_id=db.Column(db.Integer,db.ForeignKey('task.id'),nullable=False,index=True); title=db.Column(db.String(200),nullable=False); completed=db.Column(db.Boolean,default=False); position=db.Column(db.Integer,default=0)
class Dependency(db.Model):
    task_id=db.Column(db.Integer,db.ForeignKey('task.id'),primary_key=True); depends_on_id=db.Column(db.Integer,db.ForeignKey('task.id'),primary_key=True)
class TaskRecurrence(db.Model):
    task_id=db.Column(db.Integer,db.ForeignKey('task.id'),primary_key=True);frequency=db.Column(db.String(20),nullable=False);interval=db.Column(db.Integer,nullable=False,default=1)
class Attachment(db.Model):
    id=db.Column(db.Integer,primary_key=True);task_id=db.Column(db.Integer,db.ForeignKey('task.id'),nullable=False,index=True);original_filename=db.Column(db.String(255),nullable=False);storage_filename=db.Column(db.String(80),unique=True,nullable=False);mime_type=db.Column(db.String(120),nullable=False);size_bytes=db.Column(db.Integer,nullable=False);created_at=db.Column(db.DateTime(timezone=True),default=now)
class Milestone(db.Model):
    id=db.Column(db.Integer,primary_key=True);project_id=db.Column(db.Integer,db.ForeignKey('project.id'),nullable=False,index=True);title=db.Column(db.String(200),nullable=False);due_date=db.Column(db.DateTime(timezone=True));completed=db.Column(db.Boolean,default=False,nullable=False)
class FocusSession(db.Model):
    id=db.Column(db.Integer,primary_key=True); user_id=db.Column(db.Integer,db.ForeignKey('user.id'),nullable=False,index=True); task_id=db.Column(db.Integer,db.ForeignKey('task.id')); start_time=db.Column(db.DateTime(timezone=True),nullable=False); end_time=db.Column(db.DateTime(timezone=True)); duration_minutes=db.Column(db.Integer,default=0)
class BreakSession(db.Model):
    id=db.Column(db.Integer,primary_key=True); user_id=db.Column(db.Integer,db.ForeignKey('user.id'),nullable=False,index=True); start_time=db.Column(db.DateTime(timezone=True),nullable=False); end_time=db.Column(db.DateTime(timezone=True),nullable=False); duration_minutes=db.Column(db.Integer,nullable=False)
class Notification(db.Model):
    id=db.Column(db.Integer,primary_key=True); user_id=db.Column(db.Integer,db.ForeignKey('user.id'),nullable=False,index=True); task_id=db.Column(db.Integer,db.ForeignKey('task.id',ondelete='SET NULL')); title=db.Column(db.String(160),nullable=False); message=db.Column(db.String(500),nullable=False); kind=db.Column(db.String(30),nullable=False); dedupe_key=db.Column(db.String(200),unique=True,nullable=False); created_at=db.Column(db.DateTime(timezone=True),default=now,nullable=False); read_at=db.Column(db.DateTime(timezone=True))
class TaskRequest(db.Model):
    id=db.Column(db.Integer,primary_key=True);user_id=db.Column(db.Integer,db.ForeignKey('user.id'),nullable=False,index=True);client_request_id=db.Column(db.String(80),nullable=False);task_id=db.Column(db.Integer,db.ForeignKey('task.id'),nullable=False,unique=True)
    __table_args__=(db.UniqueConstraint('user_id','client_request_id',name='uq_task_request_user_key'),)
class PasswordResetToken(db.Model):
    id=db.Column(db.Integer,primary_key=True);user_id=db.Column(db.Integer,db.ForeignKey('user.id'),nullable=False,index=True);token_hash=db.Column(db.String(64),unique=True,nullable=False);expires_at=db.Column(db.DateTime(timezone=True),nullable=False);created_at=db.Column(db.DateTime(timezone=True),default=now,nullable=False);used_at=db.Column(db.DateTime(timezone=True))
class Conversation(db.Model):
    id=db.Column(db.Integer,primary_key=True); user_id=db.Column(db.Integer,db.ForeignKey('user.id'),nullable=False,index=True); title=db.Column(db.String(200)); created_at=db.Column(db.DateTime(timezone=True),default=now)
class Message(db.Model):
    id=db.Column(db.Integer,primary_key=True); conversation_id=db.Column(db.Integer,db.ForeignKey('conversation.id'),nullable=False,index=True); role=db.Column(db.String(20),nullable=False); content=db.Column(db.Text,nullable=False); created_at=db.Column(db.DateTime(timezone=True),default=now)

def fail(message,status=400):return jsonify({'error':{'message':message}}),status
def user():return User.query.get_or_404(int(get_jwt_identity()))
def mine(task_id):return Task.query.filter_by(id=task_id,user_id=user().id).first_or_404()
def accessible_project(project_id):
    current=user();project=Project.query.filter_by(id=project_id,user_id=current.id).first()
    if project:return project
    if ProjectMember.query.filter_by(project_id=project_id,user_id=current.id).first():return Project.query.get_or_404(project_id)
    abort(404)
def notify(user_id,title,message,kind='task',task_id=None,dedupe_key=None):
    key=dedupe_key or uuid.uuid4().hex
    if dedupe_key and Notification.query.filter_by(dedupe_key=key).first():return
    db.session.add(Notification(user_id=user_id,task_id=task_id,title=title,message=message,kind=kind,dedupe_key=key))
def task_data(t):return {'id':t.id,'title':t.title,'description':t.description,'status':t.status,'priority':t.priority,'category':t.category,'tags':t.tags or [],'start_date':stamp(t.start_date),'due_date':stamp(t.due_date),'scheduled_start':stamp(t.scheduled_start),'estimated_minutes':t.estimated_minutes,'actual_minutes':t.actual_minutes,'subtasks':[{'id':s.id,'title':s.title,'completed':s.completed,'position':s.position} for s in t.subtasks],'dependencies':[item.id for item in t.dependencies],'project_ids':[project.id for project in t.projects],'projects':[{'id':project.id,'name':project.name}for project in t.projects],'recurrence':{'frequency':t.recurrence.frequency,'interval':t.recurrence.interval}if t.recurrence else None}
def update(t,d):
    if 'title'in d:
        if not isinstance(d['title'],str) or not d['title'].strip() or len(d['title'])>200:raise ValueError('Title is required (maximum 200 characters).')
        t.title=d['title'].strip()
    for field,limit in [('description',5000),('category',80)]:
        if field in d:setattr(t,field,str(d[field])[:limit])
    if 'priority'in d:
        if d['priority'] not in PRIORITIES:raise ValueError('Invalid priority.')
        t.priority=d['priority']
    if 'status'in d:
        if d['status'] not in STATUSES:raise ValueError('Invalid status.')
        t.status=d['status']
    for field in ('start_date','due_date','scheduled_start'):
        if field in d:setattr(t,field,date(d[field]))
    if t.start_date and t.due_date and t.due_date<t.start_date:raise ValueError('Due date cannot precede start date.')
    for field in ('estimated_minutes','actual_minutes'):
        if field in d:
            value=int(d[field])
            if value<0 or value>100000:raise ValueError('Invalid duration.')
            setattr(t,field,value)
    if 'tags'in d:
        if not isinstance(d['tags'],list)or len(d['tags'])>20:raise ValueError('Tags must be a list of at most 20 items.')
        t.tags=[str(value)[:40]for value in d['tags']]
def assign_projects(task,data,owner_id):
    if 'project_ids' not in data:return
    ids=data['project_ids']
    if not isinstance(ids,list)or len(ids)>20:raise ValueError('Projects must be a list of at most 20 IDs.')
    try:ids=list(dict.fromkeys(int(value)for value in ids))
    except (TypeError,ValueError):raise ValueError('Project IDs must be integers.')
    projects=Project.query.filter(Project.user_id==owner_id,Project.id.in_(ids)).all()if ids else []
    if {project.id for project in projects}!=set(ids):raise ValueError('One or more projects were not found.')
    task.projects=projects
def update_recurrence(task,data):
    if 'recurrence' not in data:return
    raw=data['recurrence']
    if raw is None:
        if task.recurrence:db.session.delete(task.recurrence)
        task.recurrence=None;return
    if not isinstance(raw,dict)or raw.get('frequency')not in {'daily','weekly','monthly'}:raise ValueError('Recurrence frequency must be daily, weekly, or monthly.')
    if isinstance(raw.get('interval',1),bool):raise ValueError('Recurrence interval must be a whole number from 1 to 365.')
    try:interval=int(raw.get('interval',1))
    except (TypeError,ValueError):raise ValueError('Recurrence interval must be a whole number from 1 to 365.')
    if interval<1 or interval>365:raise ValueError('Recurrence interval must be a whole number from 1 to 365.')
    if task.recurrence:task.recurrence.frequency=raw['frequency'];task.recurrence.interval=interval
    else:task.recurrence=TaskRecurrence(frequency=raw['frequency'],interval=interval)
def next_recurrence_due(task,rule):
    anchor=task.due_date or now()
    if rule.frequency=='daily':return anchor+timedelta(days=rule.interval)
    if rule.frequency=='weekly':return anchor+timedelta(weeks=rule.interval)
    month_index=anchor.month-1+rule.interval;year=anchor.year+month_index//12;month=month_index%12+1
    return anchor.replace(year=year,month=month,day=min(anchor.day,calendar.monthrange(year,month)[1]))
def spawn_next_occurrence(task):
    rule=task.recurrence
    if not rule:return None
    next_task=Task(user_id=task.user_id,title=task.title,description=task.description,status='todo',priority=task.priority,category=task.category,tags=list(task.tags or[]),due_date=next_recurrence_due(task,rule),estimated_minutes=task.estimated_minutes or 30,actual_minutes=0)
    next_task.projects=list(task.projects);db.session.add(next_task);db.session.flush()
    next_task.recurrence=TaskRecurrence(frequency=rule.frequency,interval=rule.interval)
    return next_task
def schedule_conflicts(owner_id,start,duration,exclude_ids=()):
    if duration<=0:return []
    end=start+timedelta(minutes=duration)
    candidates=Task.query.filter(Task.user_id==owner_id,Task.status!='completed',Task.scheduled_start.isnot(None))
    if exclude_ids:candidates=candidates.filter(~Task.id.in_(exclude_ids))
    return [task for task in candidates.all()if task.scheduled_start<end and start<task.scheduled_start+timedelta(minutes=task.estimated_minutes or 0)]
def project_data(project):
    tasks=list(project.tasks);milestones=project.milestones
    return {'id':project.id,'name':project.name,'description':project.description,'task_ids':[task.id for task in tasks],'task_count':len(tasks),'completed_tasks':sum(task.status=='completed'for task in tasks),'progress_percentage':round(100*sum(task.status=='completed'for task in tasks)/len(tasks))if tasks else 0,'milestones':[{'id':item.id,'title':item.title,'due_date':stamp(item.due_date),'completed':item.completed}for item in milestones]}
def attachment_data(item):
    return {'id':item.id,'task_id':item.task_id,'filename':item.original_filename,'mime_type':item.mime_type,'size_bytes':item.size_bytes,'created_at':stamp(item.created_at)}
    if 'tags'in d:
        if not isinstance(d['tags'],list) or len(d['tags'])>20:raise ValueError('Tags must be a list of at most 20 items.')
        t.tags=[str(x)[:40] for x in d['tags']]
def ai(system,prompt):
    provider=os.getenv('AI_PROVIDER','openai').lower();local=provider=='ollama';key=os.getenv('AI_API_KEY','ollama'if local else '');base=os.getenv('AI_BASE_URL','http://localhost:11434/v1'if local else 'https://api.openai.com/v1').rstrip('/');model=os.getenv('AI_MODEL','qwen3:4b'if local else 'gpt-4o-mini')
    if not key:raise RuntimeError('AI is not configured. Add AI_API_KEY to backend/.env, or select the free local Ollama provider.')
    response=requests.post(base+'/chat/completions',headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},json={'model':model,'messages':[{'role':'system','content':system},{'role':'user','content':prompt}],'response_format':{'type':'json_object'},'temperature':.3},timeout=120 if local else 30)
    if response.status_code>=400:raise RuntimeError(('Local Ollama request failed. Install/start Ollama and download the selected model.'if local else 'AI provider request failed. Check its key, model, and availability.'))
    try:return json.loads(response.json()['choices'][0]['message']['content'])
    except Exception:raise RuntimeError('AI returned invalid structured JSON.')

def create_app(test_config=None):
    app=Flask(__name__); app.config.update(SECRET_KEY=os.getenv('SECRET_KEY','dev-change-me'),JWT_SECRET_KEY=os.getenv('JWT_SECRET_KEY','dev-jwt-change-me'),SQLALCHEMY_DATABASE_URI=os.getenv('DATABASE_URL','sqlite:///taskmaster.db'),SQLALCHEMY_TRACK_MODIFICATIONS=False,JWT_ACCESS_TOKEN_EXPIRES=timedelta(hours=8),MAX_CONTENT_LENGTH=20*1024*1024);app.config.update(test_config or {})
    db.init_app(app);migrate.init_app(app,db);jwt.init_app(app);limiter.init_app(app);CORS(app,origins=os.getenv('CORS_ORIGINS','http://localhost:5173').split(','))
    @jwt.token_in_blocklist_loader
    def reset_revokes_old_tokens(jwt_header,jwt_payload):
        try:user_id=int(jwt_payload['sub'])
        except (KeyError,TypeError,ValueError):return True
        account=User.query.get(user_id)
        if account is None:return True
        current_version=(account.preferences or{}).get('auth_version',0)
        if type(current_version)is not int:current_version=0
        return jwt_payload.get('auth_version',0)!=current_version
    @app.get('/')
    def home():
        return jsonify({'service':'TaskMaster Pro API','status':'ok','frontend':'http://localhost:5173','openapi':'/api/openapi.json','swagger_ui':'/api/docs'})
    @app.get('/api/health')
    def health():
        return jsonify({'status':'ok'})
    @app.get('/api/docs')
    def docs():
        page='''<!doctype html><html><head><meta charset="utf-8"><title>TaskMaster Pro API Docs</title><link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css"></head><body><div id="swagger-ui"></div><script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script><script>SwaggerUIBundle({url:'/api/openapi.json',dom_id:'#swagger-ui',persistAuthorization:true})</script></body></html>'''
        return Response(page,mimetype='text/html')
    @app.errorhandler(404)
    def missing(error):return fail('Resource not found.',404)
    @app.errorhandler(429)
    def limited(error):return fail('Too many requests. Please try later.',429)
    @app.errorhandler(413)
    def too_large(error):return fail('Uploaded file exceeds the 20 MB limit.',413)
    @app.post('/api/auth/register')
    @limiter.limit('10/hour')
    def register():
        d=request.get_json(silent=True)or{};email=str(d.get('email','')).lower().strip();name=str(d.get('name','')).strip();password=d.get('password','')
        if not name or len(name)>120 or '@'not in email or len(password)<8:return fail('Provide a name, valid email, and password of at least 8 characters.')
        if User.query.filter_by(email=email).first():return fail('Email is already registered.',409)
        u=User(name=name,email=email,password_hash=generate_password_hash(password));db.session.add(u);db.session.commit();return jsonify({'user':{'id':u.id,'name':u.name,'email':u.email},'access_token':create_access_token(identity=str(u.id),additional_claims={'auth_version':(u.preferences or{}).get('auth_version',0)})}),201
    @app.post('/api/auth/login')
    @limiter.limit('10/minute')
    def login():
        d=request.get_json(silent=True)or{};u=User.query.filter_by(email=str(d.get('email','')).lower().strip()).first()
        if not u or not check_password_hash(u.password_hash,d.get('password','')):return fail('Invalid email or password.',401)
        return jsonify({'user':{'id':u.id,'name':u.name,'email':u.email},'access_token':create_access_token(identity=str(u.id),additional_claims={'auth_version':(u.preferences or{}).get('auth_version',0)})})
    @app.post('/api/auth/password-reset/request')
    @limiter.limit('5/hour')
    def password_reset_request():
        host=os.getenv('SMTP_HOST');sender=os.getenv('SMTP_FROM')
        if not host or not sender:return fail('Password reset is unavailable until SMTP_HOST and SMTP_FROM are configured.',503)
        try:port=int(os.getenv('SMTP_PORT','587'))
        except ValueError:return fail('SMTP_PORT must be a valid port number.',503)
        if not 1<=port<=65535:return fail('SMTP_PORT must be between 1 and 65535.',503)
        data=request.get_json(silent=True)
        if not isinstance(data,dict):return fail('Email address is required.')
        username=os.getenv('SMTP_USERNAME');password=os.getenv('SMTP_PASSWORD')
        if bool(username)!=bool(password):return fail('Configure both SMTP_USERNAME and SMTP_PASSWORD, or neither.',503)
        email=str(data.get('email','')).strip().lower();u=User.query.filter_by(email=email).first()
        if not u:return jsonify({'message':'If that account exists, a password reset link has been sent.'}),202
        token=secrets.token_urlsafe(32);record=PasswordResetToken(user_id=u.id,token_hash=hashlib.sha256(token.encode()).hexdigest(),expires_at=now()+timedelta(minutes=30))
        PasswordResetToken.query.filter_by(user_id=u.id,used_at=None).update({'used_at':now()},synchronize_session=False);db.session.add(record);db.session.commit()
        base=os.getenv('FRONTEND_URL','http://localhost:5173').rstrip('/');url=f'{base}/#reset_token={quote(token)}'
        message=EmailMessage();message['Subject']='Reset your TaskMaster Pro password';message['From']=sender;message['To']=u.email;message.set_content(f'Use this one-time link within 30 minutes to reset your password:\n\n{url}\n\nIf you did not request this, ignore this email.')
        try:
            use_ssl=os.getenv('SMTP_USE_SSL','false').lower()=='true';use_tls=os.getenv('SMTP_USE_TLS','true').lower()=='true'
            client=smtplib.SMTP_SSL(host,port,timeout=15,context=ssl.create_default_context())if use_ssl else smtplib.SMTP(host,port,timeout=15)
            with client:
                if use_tls and not use_ssl:client.starttls(context=ssl.create_default_context())
                if username and password:client.login(username,password)
                client.send_message(message)
        except (OSError,smtplib.SMTPException) as e:
            app.logger.exception('Password reset email delivery failed')
            record.used_at=now();db.session.commit()
            return fail('Password reset email could not be sent. Check the SMTP service configuration and try again.',503)
        return jsonify({'message':'If that account exists, a password reset link has been sent.'}),202
    @app.post('/api/auth/password-reset/confirm')
    @limiter.limit('10/hour')
    def password_reset_confirm():
        data=request.get_json(silent=True)
        if not isinstance(data,dict):return fail('A reset token and new password are required.')
        token=data.get('token');password=data.get('password')
        if not isinstance(token,str)or len(token)>200 or not isinstance(password,str)or len(password)<8:return fail('Provide a valid reset token and a password of at least 8 characters.')
        digest=hashlib.sha256(token.encode()).hexdigest();record=PasswordResetToken.query.filter_by(token_hash=digest).first()
        current=now()
        if not record or record.used_at or record.expires_at<=current:return fail('This password reset link is invalid or expired.',400)
        account=User.query.get_or_404(record.user_id);account.password_hash=generate_password_hash(password);current_version=(account.preferences or{}).get('auth_version',0);current_version=current_version if type(current_version)is int else 0;account.preferences={**(account.preferences or{}),'auth_version':current_version+1};record.used_at=current
        PasswordResetToken.query.filter(PasswordResetToken.user_id==account.id,PasswordResetToken.id!=record.id,PasswordResetToken.used_at.is_(None)).update({'used_at':current},synchronize_session=False)
        db.session.commit();return jsonify({'message':'Password changed. You can now sign in.'})
    @app.get('/api/auth/me')
    @jwt_required()
    def me():
        u=user();return jsonify({'id':u.id,'name':u.name,'email':u.email,'preferences':u.preferences or {}})
    @app.post('/api/auth/logout')
    def logout():return jsonify({'message':'Discard the access token on the client.'})
    @app.route('/api/tasks',methods=['GET','POST'])
    @jwt_required()
    def tasks():
        u=user()
        if request.method=='GET':
            q=Task.query.filter_by(user_id=u.id)
            search=request.args.get('q','').strip()
            if len(search)>200:return fail('Search query is limited to 200 characters.')
            if search:
                term='%'+search+'%'
                q=q.filter(or_(Task.title.ilike(term),Task.description.ilike(term),Task.category.ilike(term),cast(Task.tags,String).ilike(term)))
            for field in ('status','priority','category'):
                if request.args.get(field):q=q.filter(getattr(Task,field)==request.args[field])
            return jsonify({'tasks':[task_data(t) for t in q.order_by(Task.due_date.asc().nullslast(),Task.created_at.desc()).all()]})
        try:
            d=request.get_json(silent=True)or{};client_request_id=d.get('client_request_id')
            if client_request_id is not None:
                if not isinstance(client_request_id,str)or not client_request_id.strip()or len(client_request_id)>80:return fail('client_request_id must be a non-empty string of at most 80 characters.')
                previous=TaskRequest.query.filter_by(user_id=u.id,client_request_id=client_request_id).first()
                if previous:return jsonify({'task':task_data(Task.query.filter_by(id=previous.task_id,user_id=u.id).first_or_404()),'duplicate_request':True}),200
            t=Task(user_id=u.id);update(t,d);assign_projects(t,d,u.id);update_recurrence(t,d);db.session.add(t);db.session.flush()
            for i,s in enumerate(d.get('subtasks',[])):
                if isinstance(s,dict) and str(s.get('title','')).strip():db.session.add(Subtask(task_id=t.id,title=str(s['title'])[:200],position=i))
            if client_request_id:db.session.add(TaskRequest(user_id=u.id,client_request_id=client_request_id,task_id=t.id))
            db.session.flush();notify(u.id,'Task added',f'“{t.title}” was added to your task list.','task',t.id);db.session.commit();return jsonify({'task':task_data(t)}),201
        except (ValueError,TypeError)as e:return fail(str(e))
    @app.route('/api/tasks/<int:task_id>',methods=['GET','PUT','DELETE'])
    @jwt_required()
    def task(task_id):
        t=mine(task_id)
        if request.method=='GET':return jsonify({'task':task_data(t)})
        if request.method=='DELETE':
            Dependency.query.filter(or_(Dependency.task_id==t.id,Dependency.depends_on_id==t.id)).delete(synchronize_session=False)
            TaskAssignment.query.filter_by(task_id=t.id).delete(synchronize_session=False)
            Notification.query.filter_by(task_id=t.id).update({'task_id':None},synchronize_session=False)
            TaskRequest.query.filter_by(task_id=t.id).delete(synchronize_session=False)
            notify(t.user_id,'Task deleted',f'“{t.title}” was removed from your task list.','task')
            for attachment in t.attachments:
                try:(Path(app.instance_path)/'uploads'/attachment.storage_filename).unlink()
                except FileNotFoundError:pass
            db.session.delete(t);db.session.commit();return '',204
        try:
            d=request.get_json(silent=True)or{}
            if 'scheduled_start'in d and d['scheduled_start']:
                proposed=date(d['scheduled_start']);overlaps=schedule_conflicts(user().id,proposed,int(d.get('estimated_minutes',t.estimated_minutes or 0)),(t.id,))
                if overlaps:return fail('Schedule conflicts with: '+', '.join(item.title for item in overlaps),409)
            previous=t.status;previous_due=stamp(t.due_date);previous_schedule=stamp(t.scheduled_start);update(t,d);assign_projects(t,d,user().id);update_recurrence(t,d)
            next_task=spawn_next_occurrence(t)if previous!='completed'and t.status=='completed'else None
            if previous!=t.status and t.status=='completed':notify(t.user_id,'Task completed',f'“{t.title}” was completed.','completion',t.id)
            if 'due_date'in d and previous_due!=stamp(t.due_date):notify(t.user_id,'Deadline updated',f'The deadline for “{t.title}” was changed.','deadline',t.id)
            if 'scheduled_start'in d and previous_schedule!=stamp(t.scheduled_start):notify(t.user_id,'Task rescheduled',f'“{t.title}” was assigned a new time.','schedule',t.id)
            if next_task:notify(t.user_id,'Recurring task created',f'“{next_task.title}” has a new occurrence.','task',next_task.id)
            db.session.commit();response={'task':task_data(t)}
            if next_task:response['next_occurrence']=task_data(next_task)
            return jsonify(response)
        except (ValueError,TypeError)as e:return fail(str(e))
    @app.patch('/api/tasks/<int:task_id>/status')
    @jwt_required()
    def status(task_id):
        try:
            t=mine(task_id);previous=t.status;update(t,{'status':(request.get_json(silent=True)or{}).get('status')});next_task=spawn_next_occurrence(t)if previous!='completed'and t.status=='completed'else None
            if previous!=t.status and t.status=='completed':notify(t.user_id,'Task completed',f'“{t.title}” was completed.','completion',t.id)
            if next_task:notify(t.user_id,'Recurring task created',f'“{next_task.title}” has a new occurrence.','task',next_task.id)
            db.session.commit();response={'task':task_data(t)}
            if next_task:response['next_occurrence']=task_data(next_task)
            return jsonify(response)
        except ValueError as e:return fail(str(e))
    @app.post('/api/tasks/<int:task_id>/subtasks')
    @jwt_required()
    def subtask(task_id):
        t=mine(task_id);title=str((request.get_json(silent=True)or{}).get('title','')).strip()
        if not title:return fail('Subtask title is required.')
        s=Subtask(task_id=t.id,title=title[:200],position=len(t.subtasks));db.session.add(s);db.session.commit();return jsonify({'subtask':{'id':s.id,'title':s.title,'completed':s.completed}}),201
    @app.post('/api/tasks/<int:task_id>/subtasks/bulk')
    @jwt_required()
    def subtasks_bulk(task_id):
        t=mine(task_id);items=(request.get_json(silent=True)or{}).get('subtasks')
        if not isinstance(items,list)or not 1<=len(items)<=20:return fail('Provide between 1 and 20 subtasks.')
        titles=[str(item.get('title','')).strip()for item in items if isinstance(item,dict)]
        if len(titles)!=len(items)or any(not title or len(title)>200 for title in titles):return fail('Each subtask needs a title of 1 to 200 characters.')
        created=[Subtask(task_id=t.id,title=title,position=len(t.subtasks)+index)for index,title in enumerate(titles)]
        db.session.add_all(created);db.session.commit()
        return jsonify({'subtasks':[{'id':s.id,'title':s.title,'completed':s.completed,'position':s.position}for s in created]}),201
    @app.route('/api/tasks/<int:task_id>/attachments',methods=['GET','POST'])
    @jwt_required()
    def task_attachments(task_id):
        t=mine(task_id)
        if request.method=='GET':return jsonify({'attachments':[attachment_data(item)for item in t.attachments]})
        upload=request.files.get('file')
        if not upload or not upload.filename:return fail('Choose a file to upload.')
        original=secure_filename(upload.filename)
        if not original:return fail('Filename is invalid.')
        extension=original.rsplit('.',1)[-1].lower()if '.'in original else''
        if extension not in {'pdf','txt','md','csv','docx','xlsx','png','jpg','jpeg','gif','webp'}:return fail('File type is not allowed.')
        directory=Path(app.instance_path)/'uploads';directory.mkdir(parents=True,exist_ok=True)
        stored_name=uuid.uuid4().hex+'.'+extension;path=directory/stored_name;upload.save(path);size=path.stat().st_size
        if not size:
            path.unlink();return fail('Empty files cannot be attached.')
        if size>20*1024*1024:
            path.unlink();return fail('Uploaded file exceeds the 20 MB limit.',413)
        item=Attachment(task_id=t.id,original_filename=original[:255],storage_filename=stored_name,mime_type=mimetypes.guess_type(original)[0]or'application/octet-stream',size_bytes=size)
        try:db.session.add(item);db.session.commit()
        except SQLAlchemyError:
            db.session.rollback();path.unlink();return fail('Attachment could not be saved.')
        return jsonify({'attachment':attachment_data(item)}),201
    @app.route('/api/attachments/<int:attachment_id>',methods=['GET','DELETE'])
    @jwt_required()
    def attachment(attachment_id):
        item=Attachment.query.join(Task).filter(Attachment.id==attachment_id,Task.user_id==user().id).first_or_404()
        path=Path(app.instance_path)/'uploads'/item.storage_filename
        if request.method=='GET':
            if not path.is_file():return fail('Attachment file is missing.',404)
            return send_file(path,as_attachment=True,download_name=item.original_filename,mimetype=item.mime_type)
        try:path.unlink()
        except FileNotFoundError:pass
        db.session.delete(item);db.session.commit();return '',204
    @app.route('/api/tasks/<int:task_id>/dependencies',methods=['GET','PUT'])
    @jwt_required()
    def task_dependencies(task_id):
        t=mine(task_id)
        if request.method=='GET':
            return jsonify({'dependencies':[{'id':item.id,'title':item.title,'status':item.status}for item in t.dependencies]})
        ids=(request.get_json(silent=True)or{}).get('depends_on_ids')
        if not isinstance(ids,list)or len(ids)>100:return fail('Provide a list of at most 100 dependency task IDs.')
        try:ids=list(dict.fromkeys(int(value)for value in ids))
        except (TypeError,ValueError):return fail('Dependency IDs must be integers.')
        if t.id in ids:return fail('A task cannot depend on itself.')
        owned={item.id for item in Task.query.filter(Task.user_id==user().id,Task.id.in_(ids)).all()}if ids else set()
        if owned!=set(ids):return fail('One or more dependency tasks were not found.',404)
        edges={}
        for source,target in db.session.query(Dependency.task_id,Dependency.depends_on_id).join(Task,Task.id==Dependency.task_id).filter(Task.user_id==user().id).all():
            edges.setdefault(source,[]).append(target)
        for dependency_id in ids:
            pending=[dependency_id];visited=set()
            while pending:
                current=pending.pop()
                if current==t.id:return fail('This dependency would create a cycle.')
                if current not in visited:
                    visited.add(current);pending.extend(edges.get(current,[]))
        Dependency.query.filter_by(task_id=t.id).delete(synchronize_session=False)
        db.session.add_all([Dependency(task_id=t.id,depends_on_id=dependency_id)for dependency_id in ids]);db.session.commit()
        return jsonify({'dependencies':[{'id':item.id,'title':item.title,'status':item.status}for item in t.dependencies]})
    @app.route('/api/projects',methods=['GET','POST'])
    @jwt_required()
    def projects():
        u=user()
        if request.method=='GET':return jsonify({'projects':[project_data(p)for p in Project.query.filter_by(user_id=u.id).order_by(Project.name).all()]})
        d=request.get_json(silent=True)or{};name=str(d.get('name','')).strip();description=str(d.get('description','')).strip()
        if not name or len(name)>120:return fail('Project name is required and limited to 120 characters.')
        if len(description)>2000:return fail('Project description is limited to 2000 characters.')
        if Project.query.filter_by(user_id=u.id,name=name).first():return fail('A project with this name already exists.',409)
        project=Project(user_id=u.id,name=name,description=description);db.session.add(project);db.session.commit()
        return jsonify({'project':project_data(project)}),201
    @app.route('/api/projects/<int:project_id>',methods=['GET','PUT','DELETE'])
    @jwt_required()
    def project(project_id):
        item=Project.query.filter_by(id=project_id,user_id=user().id).first_or_404()
        if request.method=='GET':return jsonify({'project':project_data(item)})
        if request.method=='DELETE':
            item.tasks.clear();ProjectMember.query.filter_by(project_id=item.id).delete(synchronize_session=False);ProjectComment.query.filter_by(project_id=item.id).delete(synchronize_session=False);TaskAssignment.query.filter_by(project_id=item.id).delete(synchronize_session=False);db.session.delete(item);db.session.commit();return '',204
        d=request.get_json(silent=True)or{}
        if 'name'in d:
            name=str(d['name']).strip()
            if not name or len(name)>120:return fail('Project name is required and limited to 120 characters.')
            duplicate=Project.query.filter(Project.user_id==item.user_id,Project.name==name,Project.id!=item.id).first()
            if duplicate:return fail('A project with this name already exists.',409)
            item.name=name
        if 'description'in d:
            description=str(d['description']).strip()
            if len(description)>2000:return fail('Project description is limited to 2000 characters.')
            item.description=description
        db.session.commit();return jsonify({'project':project_data(item)})
    @app.put('/api/projects/<int:project_id>/tasks')
    @jwt_required()
    def project_tasks_update(project_id):
        item=Project.query.filter_by(id=project_id,user_id=user().id).first_or_404();ids=(request.get_json(silent=True)or{}).get('task_ids')
        if not isinstance(ids,list)or len(ids)>1000:return fail('Task IDs must be a list of at most 1000 items.')
        try:ids=list(dict.fromkeys(int(value)for value in ids))
        except (TypeError,ValueError):return fail('Task IDs must be integers.')
        tasks=Task.query.filter(Task.user_id==item.user_id,Task.id.in_(ids)).all()if ids else []
        if {task.id for task in tasks}!=set(ids):return fail('One or more tasks were not found.',404)
        removed_ids={task.id for task in item.tasks}-set(ids)
        if removed_ids:TaskAssignment.query.filter(TaskAssignment.project_id==item.id,TaskAssignment.task_id.in_(removed_ids)).delete(synchronize_session=False)
        item.tasks=tasks;db.session.commit();return jsonify({'project':project_data(item)})
    @app.route('/api/projects/<int:project_id>/members',methods=['GET','POST'])
    @jwt_required()
    def project_members(project_id):
        item=Project.query.filter_by(id=project_id,user_id=user().id).first_or_404()
        if request.method=='GET':
            rows=ProjectMember.query.filter_by(project_id=item.id).all()
            return jsonify({'members':[{'id':row.user_id,'name':User.query.get(row.user_id).name,'email':User.query.get(row.user_id).email,'added_at':stamp(row.created_at)}for row in rows]})
        email=str((request.get_json(silent=True)or{}).get('email','')).strip().lower();member=User.query.filter_by(email=email).first()
        if not member:return fail('The invited user must already have an account.',404)
        if member.id==item.user_id:return fail('The project owner is already a member.')
        if ProjectMember.query.filter_by(project_id=item.id,user_id=member.id).first():return fail('This user is already a project member.',409)
        row=ProjectMember(project_id=item.id,user_id=member.id,invited_by=user().id);db.session.add(row);notify(member.id,'Project shared',f'{user().name} shared the project “{item.name}” with you.','collaboration');db.session.commit()
        return jsonify({'member':{'id':member.id,'name':member.name,'email':member.email,'added_at':stamp(row.created_at)}}),201
    @app.delete('/api/projects/<int:project_id>/members/<int:member_id>')
    @jwt_required()
    def project_member_remove(project_id,member_id):
        item=Project.query.filter_by(id=project_id,user_id=user().id).first_or_404();row=ProjectMember.query.filter_by(project_id=item.id,user_id=member_id).first_or_404()
        TaskAssignment.query.filter_by(project_id=item.id,user_id=member_id).delete(synchronize_session=False);notify(member_id,'Project access removed',f'Your access to “{item.name}” was removed.','collaboration');db.session.delete(row);db.session.commit();return '',204
    @app.route('/api/projects/<int:project_id>/comments',methods=['GET','POST'])
    @jwt_required()
    def project_comments(project_id):
        item=accessible_project(project_id)
        if request.method=='GET':
            comments=ProjectComment.query.filter_by(project_id=item.id).order_by(ProjectComment.created_at.asc(),ProjectComment.id.asc()).limit(500).all()
            authors={u.id:u.name for u in User.query.filter(User.id.in_({c.author_id for c in comments})).all()}if comments else{}
            return jsonify({'comments':[{'id':c.id,'author_id':c.author_id,'author':authors.get(c.author_id,'Unknown user'),'message':c.message,'created_at':stamp(c.created_at)}for c in comments]})
        message=str((request.get_json(silent=True)or{}).get('message','')).strip()
        if not message or len(message)>2000:return fail('Comment text is required and limited to 2000 characters.')
        current=user();comment=ProjectComment(project_id=item.id,author_id=current.id,message=message);db.session.add(comment)
        if current.id!=item.user_id:notify(item.user_id,'Project comment',f'{current.name} commented on “{item.name}”.','collaboration')
        db.session.commit();return jsonify({'comment':{'id':comment.id,'author_id':current.id,'author':current.name,'message':comment.message,'created_at':stamp(comment.created_at)}}),201
    @app.route('/api/projects/<int:project_id>/assignments',methods=['GET','PUT'])
    @jwt_required()
    def project_assignments(project_id):
        item=Project.query.filter_by(id=project_id,user_id=user().id).first_or_404()
        if request.method=='GET':
            rows=TaskAssignment.query.filter_by(project_id=item.id).all()
            return jsonify({'assignments':[{'task_id':row.task_id,'user_id':row.user_id,'name':User.query.get(row.user_id).name,'email':User.query.get(row.user_id).email}for row in rows]})
        entries=(request.get_json(silent=True)or{}).get('assignments')
        if not isinstance(entries,list)or len(entries)>1000:return fail('Assignments must be a list of at most 1000 entries.')
        try:pairs=list(dict.fromkeys((int(row['task_id']),int(row['user_id']))for row in entries if isinstance(row,dict)))
        except (KeyError,TypeError,ValueError):return fail('Each assignment requires integer task_id and user_id values.')
        if len(pairs)!=len(entries):return fail('Each assignment must be a unique task/member pair.')
        task_ids={row[0]for row in pairs};member_ids={row[1]for row in pairs}
        project_task_ids={t.id for t in item.tasks}
        if not task_ids<=project_task_ids:return fail('Assignments can only include tasks in this project.')
        eligible={m.user_id for m in ProjectMember.query.filter(ProjectMember.project_id==item.id,ProjectMember.user_id.in_(member_ids)).all()}if member_ids else set()
        if eligible!=member_ids:return fail('Every assignee must be a current project member.')
        existing_pairs={(row.task_id,row.user_id)for row in TaskAssignment.query.filter_by(project_id=item.id).all()}
        TaskAssignment.query.filter_by(project_id=item.id).delete(synchronize_session=False)
        new_pairs=set(pairs)
        for task_id,member_id in existing_pairs-new_pairs:
            task=next(task for task in item.tasks if task.id==task_id);notify(member_id,'Task unassigned',f'“{task.title}” is no longer assigned to you.','collaboration',task.id)
        for task_id,member_id in pairs:
            db.session.add(TaskAssignment(project_id=item.id,task_id=task_id,user_id=member_id,assigned_by=user().id))
            if (task_id,member_id)not in existing_pairs:
                task=next(task for task in item.tasks if task.id==task_id);notify(member_id,'Task assigned',f'“{task.title}” was assigned to you in “{item.name}”.','collaboration',task.id)
        db.session.commit();return jsonify({'assignments':[{'task_id':task_id,'user_id':member_id}for task_id,member_id in pairs]})
    @app.get('/api/shared/projects')
    @jwt_required()
    def shared_projects():
        memberships=ProjectMember.query.filter_by(user_id=user().id).all();ids=[row.project_id for row in memberships]
        projects=Project.query.filter(Project.id.in_(ids)).order_by(Project.name).all()if ids else[]
        response=[]
        for item in projects:
            owner=User.query.get(item.user_id)
            tasks=[]
            for task in item.tasks:
                data=task_data(task);data['assigned_to_me']=TaskAssignment.query.filter_by(project_id=item.id,task_id=task.id,user_id=user().id).first()is not None;tasks.append(data)
            response.append({'project':project_data(item),'owner':owner.name if owner else'Unknown user','tasks':tasks})
        return jsonify({'projects':response})
    @app.get('/api/assigned-tasks')
    @jwt_required()
    def assigned_tasks():
        assignments=TaskAssignment.query.filter_by(user_id=user().id).order_by(TaskAssignment.created_at.desc()).all();result=[]
        for row in assignments:
            task=Task.query.filter_by(id=row.task_id).first();project=Project.query.filter_by(id=row.project_id).first()
            if task and project:result.append({'task':task_data(task),'project':project.name,'project_id':project.id,'owner_id':project.user_id})
        return jsonify({'assignments':result})
    @app.patch('/api/assigned-tasks/<int:task_id>/status')
    @jwt_required()
    def assigned_task_status(task_id):
        row=TaskAssignment.query.filter_by(task_id=task_id,user_id=user().id).first_or_404();task=Task.query.get_or_404(task_id);previous=task.status
        try:update(task,{'status':(request.get_json(silent=True)or{}).get('status')})
        except ValueError as e:return fail(str(e))
        notify(task.user_id,'Collaborator updated task',f'{user().name} changed “{task.title}” to {task.status.replace("_"," ")}.','collaboration',task.id)
        next_task=spawn_next_occurrence(task)if previous!='completed'and task.status=='completed'else None
        if next_task:notify(task.user_id,'Recurring task created',f'“{next_task.title}” has a new occurrence.','task',next_task.id)
        db.session.commit();return jsonify({'task':task_data(task),'next_occurrence':task_data(next_task)if next_task else None})
    @app.route('/api/projects/<int:project_id>/milestones',methods=['GET','POST'])
    @jwt_required()
    def project_milestones(project_id):
        item=Project.query.filter_by(id=project_id,user_id=user().id).first_or_404()
        if request.method=='GET':return jsonify({'milestones':[{'id':m.id,'title':m.title,'due_date':stamp(m.due_date),'completed':m.completed}for m in item.milestones]})
        d=request.get_json(silent=True)or{};title=str(d.get('title','')).strip()
        if not title or len(title)>200:return fail('Milestone title is required and limited to 200 characters.')
        try:due=date(d.get('due_date'))if d.get('due_date')else None
        except ValueError as e:return fail(str(e))
        milestone=Milestone(project_id=item.id,title=title,due_date=due);db.session.add(milestone);db.session.commit()
        return jsonify({'milestone':{'id':milestone.id,'title':milestone.title,'due_date':stamp(milestone.due_date),'completed':milestone.completed}}),201
    @app.route('/api/projects/<int:project_id>/milestones/<int:milestone_id>',methods=['PATCH','DELETE'])
    @jwt_required()
    def project_milestone(project_id,milestone_id):
        item=Project.query.filter_by(id=project_id,user_id=user().id).first_or_404();milestone=Milestone.query.filter_by(id=milestone_id,project_id=item.id).first_or_404()
        if request.method=='DELETE':db.session.delete(milestone);db.session.commit();return '',204
        d=request.get_json(silent=True)or{}
        if 'title'in d:
            title=str(d['title']).strip()
            if not title or len(title)>200:return fail('Milestone title is required and limited to 200 characters.')
            milestone.title=title
        if 'completed'in d:
            if not isinstance(d['completed'],bool):return fail('Milestone completed must be true or false.')
            milestone.completed=d['completed']
        if 'due_date'in d:
            try:milestone.due_date=date(d['due_date'])if d['due_date']else None
            except ValueError as e:return fail(str(e))
        db.session.commit();return jsonify({'milestone':{'id':milestone.id,'title':milestone.title,'due_date':stamp(milestone.due_date),'completed':milestone.completed}})
    @app.get('/api/export/tasks.csv')
    @jwt_required()
    def export_tasks_csv():
        output=io.StringIO(newline='');writer=csv.writer(output)
        writer.writerow(['id','title','description','status','priority','category','tags','due_date','estimated_minutes','actual_minutes','subtasks','dependencies','recurrence'])
        for task in Task.query.filter_by(user_id=user().id).order_by(Task.id).all():
            values=[task.id,task.title,task.description,task.status,task.priority,task.category,', '.join(task.tags or []),stamp(task.due_date),task.estimated_minutes,task.actual_minutes,'; '.join(item.title for item in task.subtasks),', '.join(str(item.id)for item in task.dependencies),task.recurrence.frequency if task.recurrence else '']
            writer.writerow([("'"+str(value)if isinstance(value,str)and value.startswith(('=','+','-','@','\t','\r'))else value)for value in values])
        return Response(output.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=taskmaster-tasks.csv'})
    @app.get('/api/export/calendar.ics')
    @jwt_required()
    def export_calendar():
        def escape(value):return str(value or'').replace('\\','\\\\').replace(';','\\;').replace(',','\\,').replace('\r\n','\\n').replace('\n','\\n').replace('\r','\\n')
        def utc_value(value):
            if value.tzinfo is None:value=value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        def fold(line):
            parts=[];current='';size=0
            for character in line:
                length=len(character.encode('utf-8'))
                if size+length>73:parts.append(current);current=' '+character;size=1+length
                else:current+=character;size+=length
            parts.append(current);return '\r\n'.join(parts)
        lines=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//TaskMaster Pro//Calendar Export//EN','CALSCALE:GREGORIAN','METHOD:PUBLISH']
        tasks=Task.query.filter(Task.user_id==user().id,or_(Task.scheduled_start.isnot(None),Task.due_date.isnot(None))).order_by(Task.scheduled_start.asc().nullslast(),Task.due_date.asc().nullslast()).all()
        for task in tasks:
            end=task.scheduled_start+timedelta(minutes=max(1,task.estimated_minutes or 30))if task.scheduled_start else task.due_date
            start=task.scheduled_start if task.scheduled_start else task.due_date-timedelta(minutes=max(1,task.estimated_minutes or 30))
            description=task.description or''
            if task.due_date:description+=(f'\nDeadline: {stamp(task.due_date)}')
            lines.extend(['BEGIN:VEVENT',f'UID:task-{task.id}-{task.user_id}@taskmaster-pro',f'DTSTAMP:{utc_value(task.updated_at or task.created_at)}',f'DTSTART:{utc_value(start)}',f'DTEND:{utc_value(end)}',f'SUMMARY:{escape(task.title)}',f'DESCRIPTION:{escape(description)}',f'STATUS:{"CANCELLED"if task.status=="completed"else"CONFIRMED"}',f'X-TASKMASTER-STATUS:{escape(task.status)}','END:VEVENT'])
        lines.append('END:VCALENDAR')
        content='\r\n'.join(fold(line)for line in lines)+'\r\n'
        return Response(content,mimetype='text/calendar',headers={'Content-Disposition':'attachment; filename=taskmaster-calendar.ics'})
    @app.get('/api/export/backup')
    @jwt_required()
    def export_backup():
        u=user()
        tasks=[task_data(t)for t in Task.query.filter_by(user_id=u.id).order_by(Task.id).all()]
        sessions=[{'start_time':stamp(s.start_time),'end_time':stamp(s.end_time),'duration_minutes':s.duration_minutes,'task_id':s.task_id,'session_type':'focus'}for s in FocusSession.query.filter_by(user_id=u.id).order_by(FocusSession.id).all()]
        sessions.extend({'start_time':stamp(s.start_time),'end_time':stamp(s.end_time),'duration_minutes':s.duration_minutes,'task_id':None,'session_type':'break'}for s in BreakSession.query.filter_by(user_id=u.id).order_by(BreakSession.id).all())
        projects=[project_data(p)for p in Project.query.filter_by(user_id=u.id).order_by(Project.id).all()]
        return jsonify({'schema_version':1,'exported_at':stamp(now()),'preferences':u.preferences or{},'tasks':tasks,'projects':projects,'focus_sessions':sessions})
    @app.post('/api/import/backup')
    @jwt_required()
    def import_backup():
        data=request.get_json(silent=True)or{};records=data.get('tasks')
        if data.get('schema_version')!=1 or not isinstance(records,list)or len(records)>1000:return fail('Backup must use schema version 1 and contain at most 1000 tasks.')
        u=user();created=[];old_to_new={};old_project_to_new={};project_records=data.get('projects',[])
        if not isinstance(project_records,list)or len(project_records)>100:return fail('Backup can contain at most 100 projects.')
        try:
            for record in project_records:
                if not isinstance(record,dict):raise ValueError('Each project must be an object.')
                name=str(record.get('name','')).strip();description=str(record.get('description','')).strip()
                if not name or len(name)>120 or len(description)>2000:raise ValueError('Backup contains an invalid project.')
                if record.get('id')is not None:
                    original_id=int(record['id'])
                    if original_id in old_project_to_new:raise ValueError('Backup contains duplicate project IDs.')
                project=Project(user_id=u.id,name=name,description=description);db.session.add(project);db.session.flush()
                if record.get('id')is not None:old_project_to_new[original_id]=project.id
                milestones=record.get('milestones',[])
                if not isinstance(milestones,list)or len(milestones)>500:raise ValueError('Each project can contain at most 500 milestones.')
                for milestone in milestones:
                    if not isinstance(milestone,dict):raise ValueError('Each milestone must be an object.')
                    title=str(milestone.get('title','')).strip()
                    if not title or len(title)>200:raise ValueError('Backup contains an invalid milestone.')
                    db.session.add(Milestone(project_id=project.id,title=title,due_date=date(milestone.get('due_date'))if milestone.get('due_date')else None,completed=bool(milestone.get('completed',False))))
            for record in records:
                if not isinstance(record,dict):raise ValueError('Each task must be an object.')
                subtasks=record.get('subtasks',[])
                if not isinstance(subtasks,list)or len(subtasks)>100:raise ValueError('Each task can contain at most 100 subtasks.')
                task=Task(user_id=u.id,description='',status='todo',priority='medium',category='General',estimated_minutes=30,actual_minutes=0)
                update(task,record);update_recurrence(task,record);db.session.add(task);db.session.flush();created.append((task,record,subtasks))
                project_ids=record.get('project_ids',[])
                if not isinstance(project_ids,list):raise ValueError('Task project IDs must be a list.')
                mapped_projects=[old_project_to_new.get(int(project_id))for project_id in dict.fromkeys(project_ids)]
                if any(project_id is None for project_id in mapped_projects):raise ValueError('Backup contains a project outside the imported projects.')
                task.projects=Project.query.filter(Project.user_id==u.id,Project.id.in_(mapped_projects)).all()if mapped_projects else []
                if record.get('id')is not None:
                    original_id=int(record['id'])
                    if original_id in old_to_new:raise ValueError('Backup contains duplicate task IDs.')
                    old_to_new[original_id]=task.id
            for task,record,subtasks in created:
                for position,item in enumerate(subtasks):
                    if not isinstance(item,dict)or not str(item.get('title','')).strip():raise ValueError('Every subtask needs a title.')
                    db.session.add(Subtask(task_id=task.id,title=str(item['title']).strip()[:200],completed=bool(item.get('completed',False)),position=position))
            imported_edges={}
            for task,record,_ in created:
                dependency_ids=record.get('dependencies',[])
                if not isinstance(dependency_ids,list):raise ValueError('Task dependencies must be a list.')
                for old_id in dict.fromkeys(dependency_ids):
                    target=old_to_new.get(int(old_id))
                    if target is None:raise ValueError('Backup contains a dependency outside the imported tasks.')
                    if target==task.id:raise ValueError('A task cannot depend on itself.')
                    pending=[target];visited=set()
                    while pending:
                        current=pending.pop()
                        if current==task.id:raise ValueError('Backup dependencies contain a cycle.')
                        if current not in visited:
                            visited.add(current);pending.extend(imported_edges.get(current,[]))
                    imported_edges.setdefault(task.id,[]).append(target)
                    db.session.add(Dependency(task_id=task.id,depends_on_id=target))
            sessions=data.get('focus_sessions',[])
            if not isinstance(sessions,list)or len(sessions)>5000:raise ValueError('Backup can contain at most 5000 focus sessions.')
            for item in sessions:
                if not isinstance(item,dict):raise ValueError('Each focus session must be an object.')
                start=date(item.get('start_time'));end=date(item.get('end_time'))if item.get('end_time')else None
                if not start or end and end<start:raise ValueError('Backup contains an invalid focus session date.')
                old_task_id=item.get('task_id');linked_id=old_to_new.get(int(old_task_id))if old_task_id is not None else None
                duration=round((end-start).total_seconds()/60)if end else int(item.get('duration_minutes',0));session_type=item.get('session_type','focus')
                if duration<0 or duration>100000:raise ValueError('Backup contains an invalid focus session duration.')
                if session_type=='break':
                    if not end:raise ValueError('Break sessions require an end time.')
                    db.session.add(BreakSession(user_id=u.id,start_time=start,end_time=end,duration_minutes=duration))
                elif session_type=='focus':db.session.add(FocusSession(user_id=u.id,task_id=linked_id,start_time=start,end_time=end,duration_minutes=duration))
                else:raise ValueError('Backup contains an invalid session type.')
            preferences=data.get('preferences',{})
            if not isinstance(preferences,dict):raise ValueError('Backup preferences must be an object.')
            preferences.pop('auth_version',None)
            u.preferences={**(u.preferences or{}),**preferences}
            db.session.commit()
        except (ValueError,TypeError)as e:
            db.session.rollback();return fail('Backup import failed: '+str(e))
        return jsonify({'imported_tasks':len(created)}),201
    @app.get('/api/analytics/overview')
    @jwt_required()
    def overview():
        u=user();current=now();ts=Task.query.filter_by(user_id=u.id).all();done=[t for t in ts if t.status=='completed'];late=[t for t in ts if t.status!='completed' and t.due_date and t.due_date<current];total=len(ts)
        today=current.date();week_start=today-timedelta(days=today.weekday());completion_events=Notification.query.filter_by(user_id=u.id,kind='completion').all();event_task_ids={item.task_id for item in completion_events if item.task_id};legacy_done=[t for t in done if t.id not in event_task_ids];done_dates={item.created_at.date()for item in completion_events if item.created_at}|{t.updated_at.date()for t in legacy_done if t.updated_at};streak=0;day=today if today in done_dates else today-timedelta(days=1)
        while day in done_dates:streak+=1;day-=timedelta(days=1)
        preferences=u.preferences or{};daily_goal=preferences.get('daily_goal_tasks',3);weekly_goal=preferences.get('weekly_goal_tasks',15)
        focus_minutes=sum(s.duration_minutes or 0 for s in FocusSession.query.filter_by(user_id=u.id).all());total_completions=len(completion_events)+len(legacy_done);daily_completed=sum(item.created_at.date()==today for item in completion_events if item.created_at)+sum(t.updated_at and t.updated_at.date()==today for t in legacy_done);weekly_completed=sum(item.created_at.date()>=week_start for item in completion_events if item.created_at)+sum(t.updated_at and t.updated_at.date()>=week_start for t in legacy_done)
        badges=[
            {'id':'first-task','name':'First Finish','description':'Complete your first task.','earned':total_completions>=1},
            {'id':'ten-tasks','name':'Momentum Builder','description':'Complete 10 tasks.','earned':total_completions>=10},
            {'id':'fifty-tasks','name':'Task Champion','description':'Complete 50 tasks.','earned':total_completions>=50},
            {'id':'three-day-streak','name':'On a Roll','description':'Complete tasks on three consecutive days.','earned':streak>=3},
            {'id':'focus-five-hours','name':'Deep Focus','description':'Log five hours of focus time.','earned':focus_minutes>=300},
        ]
        return jsonify({'total_tasks':total,'completed_tasks':len(done),'pending_tasks':total-len(done),'overdue_tasks':len(late),'completion_percentage':round(100*len(done)/total)if total else 0,'by_priority':{p:sum(t.priority==p for t in ts)for p in PRIORITIES},'by_category':{c:sum(t.category==c for t in ts)for c in sorted({t.category for t in ts})},'estimated_minutes':sum(t.estimated_minutes or 0 for t in ts),'actual_minutes':sum(t.actual_minutes or 0 for t in ts),'goals':{'daily':{'target':daily_goal,'completed':daily_completed},'weekly':{'target':weekly_goal,'completed':weekly_completed}},'gamification':{'points':total_completions*10+(focus_minutes//25)*5,'streak_days':streak,'badges':badges}})
    @app.get('/api/analytics/trends')
    @jwt_required()
    def trends():
        u=user();since=now().date()-timedelta(days=6);events=Notification.query.filter_by(user_id=u.id,kind='completion').all();event_task_ids={item.task_id for item in events if item.task_id};legacy=[t for t in Task.query.filter_by(user_id=u.id,status='completed').all()if t.id not in event_task_ids]
        return jsonify({'daily':[{'date':(since+timedelta(days=i)).isoformat(),'completed':sum(item.created_at.date()==since+timedelta(days=i)for item in events if item.created_at)+sum(t.updated_at and t.updated_at.date()==since+timedelta(days=i)for t in legacy)}for i in range(7)]})
    @app.post('/api/focus-sessions')
    @jwt_required()
    def focus():
        try:
            d=request.get_json(silent=True)or{};start=date(d.get('start_time'));end=date(d.get('end_time'))if d.get('end_time')else now();session_type=d.get('session_type','focus')
            if not start or end<start:return fail('Valid start and end times are required.')
            if session_type not in {'focus','break'}:return fail('session_type must be focus or break.')
            task=mine(int(d['task_id']))if d.get('task_id')and session_type=='focus'else None
            duration=round((end-start).total_seconds()/60)
            if session_type=='break':s=BreakSession(user_id=user().id,start_time=start,end_time=end,duration_minutes=duration);db.session.add(s)
            else:
                s=FocusSession(user_id=user().id,task_id=task.id if task else None,start_time=start,end_time=end,duration_minutes=duration);db.session.add(s)
                if task:task.actual_minutes=(task.actual_minutes or 0)+duration
            db.session.commit();return jsonify({'session':{'id':s.id,'duration_minutes':s.duration_minutes,'session_type':session_type}}),201
        except(ValueError,TypeError)as e:return fail(str(e))
    @app.get('/api/focus-sessions')
    @jwt_required()
    def focus_history():
        u=user();current=now();today=current.date();week_start=today-timedelta(days=6)
        focus=FocusSession.query.filter_by(user_id=u.id).order_by(FocusSession.start_time.desc()).limit(100).all()
        breaks=BreakSession.query.filter_by(user_id=u.id).order_by(BreakSession.start_time.desc()).limit(100).all()
        task_ids={s.task_id for s in focus if s.task_id};task_titles={t.id:t.title for t in Task.query.filter(Task.id.in_(task_ids),Task.user_id==u.id).all()}if task_ids else{}
        sessions=[{'id':s.id,'session_type':'focus','task_id':s.task_id,'task_title':task_titles.get(s.task_id),'start_time':stamp(s.start_time),'end_time':stamp(s.end_time),'duration_minutes':s.duration_minutes or 0}for s in focus]
        sessions.extend({'id':s.id,'session_type':'break','task_id':None,'task_title':None,'start_time':stamp(s.start_time),'end_time':stamp(s.end_time),'duration_minutes':s.duration_minutes}for s in breaks)
        sessions.sort(key=lambda item:item['start_time']or'',reverse=True)
        return jsonify({'sessions':sessions[:100],'stats':{'focus_minutes_today':sum(s.duration_minutes or 0 for s in focus if s.start_time.date()==today),'break_minutes_today':sum(s.duration_minutes or 0 for s in breaks if s.start_time.date()==today),'focus_minutes_week':sum(s.duration_minutes or 0 for s in FocusSession.query.filter(FocusSession.user_id==u.id,FocusSession.start_time>=datetime.combine(week_start,datetime.min.time())).all()),'sessions_total':len(focus)+len(breaks)}})
    @app.post('/api/ai/generate-tasks')
    @jwt_required()
    @limiter.limit('15/hour')
    def generate_tasks():
        prompt=str((request.get_json(silent=True)or{}).get('prompt',''))
        if not prompt or len(prompt)>4000:return fail('Prompt is required and limited to 4000 characters.')
        try:data=ai('Return JSON only: {"tasks":[{"title":string,"description":string,"priority":"low|medium|high|urgent","start_date":ISO|null,"due_date":ISO|null,"estimated_minutes":number,"category":string,"tags":[string],"subtasks":[{"title":string}]}]}. Never save tasks.',prompt)
        except RuntimeError as e:return fail(str(e),503)
        if not isinstance(data.get('tasks'),list):return fail('AI response failed validation.',502)
        cleaned=[]
        for raw in data['tasks'][:20]:
            try:
                t=Task(user_id=user().id,description='',status='todo',priority='medium',category='General',estimated_minutes=30,actual_minutes=0);update(t,raw)
                cleaned.append({**task_data(t),'id':None,'subtasks':[{'title':str(s.get('title','')).strip()[:200]}for s in raw.get('subtasks',[])[:20]if isinstance(s,dict)and str(s.get('title','')).strip()]})
            except (ValueError,TypeError):pass
        return jsonify({'tasks':cleaned,'message':'Review and approve each task before saving.'})
    @app.post('/api/ai/chat')
    @jwt_required()
    @limiter.limit('20/hour')
    def chat():
        d=request.get_json(silent=True)or{};question=str(d.get('message','')).strip();u=user()
        if not question or len(question)>4000:return fail('Message is required.')
        c=Conversation.query.filter_by(id=d.get('conversation_id'),user_id=u.id).first()if d.get('conversation_id')else Conversation(user_id=u.id,title=question[:60])
        if not c.id:db.session.add(c);db.session.flush()
        db.session.add(Message(conversation_id=c.id,role='user',content=question));tasks=[{'title':t.title,'status':t.status,'priority':t.priority,'due_date':stamp(t.due_date)}for t in Task.query.filter_by(user_id=u.id).limit(100)]
        try:data=ai('You are TaskMaster Pro. Return JSON {"reply":string,"proposed_actions":[]}. Use supplied task context only. Propose consequential changes but never execute them.',json.dumps({'question':question,'tasks':tasks}))
        except RuntimeError as e:return fail(str(e),503)
        reply=str(data.get('reply',''))[:6000];db.session.add(Message(conversation_id=c.id,role='assistant',content=reply));db.session.commit();return jsonify({'conversation_id':c.id,'reply':reply,'proposed_actions':data.get('proposed_actions',[])})
    @app.post('/api/ai/schedule')
    @jwt_required()
    def schedule():
        d=request.get_json(silent=True)or{};prefs=user().preferences or{}
        try:start=int(d.get('start_hour',prefs.get('work_start_hour',9)));end=int(d.get('end_hour',prefs.get('work_end_hour',17)))
        except (TypeError,ValueError):return fail('Working hours must be whole hours.')
        if not 0<=start<end<=24:return fail('Working hours are invalid.')
        owner_id=user().id;all_tasks=Task.query.filter_by(user_id=owner_id).all()
        rank={'urgent':0,'high':1,'medium':2,'low':3};pending=[t for t in all_tasks if t.status!='completed'and not t.scheduled_start];pending.sort(key=lambda t:(rank[t.priority],t.due_date or datetime.max))
        current=now();cursor=current.replace(hour=start,minute=0,second=0,microsecond=0);cursor=max(current,cursor)
        busy=[(t.scheduled_start,t.scheduled_start+timedelta(minutes=t.estimated_minutes or 0))for t in all_tasks if t.status!='completed'and t.scheduled_start]
        result=[];unscheduled=[]
        for t in pending:
            duration=max(0,t.estimated_minutes or 0)
            if duration>(end-start)*60:
                unscheduled.append({'task_id':t.id,'title':t.title,'reason':'Estimated duration exceeds the configured workday.'})
                continue
            while True:
                day_start=cursor.replace(hour=start,minute=0,second=0,microsecond=0)
                day_end=cursor.replace(hour=end%24,minute=0,second=0,microsecond=0)
                if end==24:day_end=cursor.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)
                cursor=max(cursor,day_start)
                if cursor>=day_end:
                    cursor=(cursor+timedelta(days=1)).replace(hour=start,minute=0,second=0,microsecond=0);continue
                collision=next(((busy_start,busy_end)for busy_start,busy_end in busy if busy_start<cursor+timedelta(minutes=duration)and cursor<busy_end),None)
                if collision:
                    cursor=collision[1];continue
                if cursor+timedelta(minutes=duration)>day_end:
                    cursor=(cursor+timedelta(days=1)).replace(hour=start,minute=0,second=0,microsecond=0);continue
                break
            suggested=cursor;busy.append((suggested,suggested+timedelta(minutes=duration)))
            result.append({'task_id':t.id,'title':t.title,'suggested_start':stamp(suggested),'duration_minutes':duration,'reason':t.priority.title()+' priority'+('; deadline-aware'if t.due_date else '')});cursor=suggested+timedelta(minutes=duration)
        return jsonify({'schedule':result,'unscheduled':unscheduled,'message':'Review this schedule and apply it to save task times.'})
    @app.post('/api/calendar/conflicts')
    @jwt_required()
    def calendar_conflicts():
        d=request.get_json(silent=True)or{}
        try:
            start=date(d.get('scheduled_start'));duration=int(d.get('duration_minutes',30));task_id=int(d['task_id'])if d.get('task_id')is not None else None
            if not start or duration<1 or duration>100000:raise ValueError()
        except (ValueError,TypeError):return fail('Provide a valid scheduled_start and duration_minutes.')
        owner_id=user().id
        if task_id:mine(task_id)
        conflicts=schedule_conflicts(owner_id,start,duration,(task_id,)if task_id else())
        suggestions=[]
        if conflicts:
            prefs=user().preferences or{}
            try:work_start=int(prefs.get('work_start_hour',9));work_end=int(prefs.get('work_end_hour',17))
            except (TypeError,ValueError):work_start,work_end=9,17
            if not 0<=work_start<work_end<=24:work_start,work_end=9,17
            candidate=start
            for _ in range(500):
                day_start=candidate.replace(hour=work_start,minute=0,second=0,microsecond=0)
                day_end=candidate.replace(hour=work_end%24,minute=0,second=0,microsecond=0)
                if work_end==24:day_end=candidate.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)
                candidate=max(candidate,day_start)
                if candidate+timedelta(minutes=duration)>day_end:
                    candidate=(candidate+timedelta(days=1)).replace(hour=work_start,minute=0,second=0,microsecond=0);continue
                overlaps=schedule_conflicts(owner_id,candidate,duration,(task_id,)if task_id else())
                overlaps.extend([{'start':value,'end':value+timedelta(minutes=duration)}for value in suggestions if value<candidate+timedelta(minutes=duration)and candidate<value+timedelta(minutes=duration)])
                if overlaps:
                    next_start=max((item['end']if isinstance(item,dict)else item.scheduled_start+timedelta(minutes=item.estimated_minutes or 0))for item in overlaps)
                    candidate=max(next_start,candidate+timedelta(minutes=15));continue
                suggestions.append(candidate)
                if len(suggestions)>=3:break
                candidate+=timedelta(minutes=duration+15)
                if candidate-start>timedelta(days=14):break
        return jsonify({'conflicts':[{'task_id':item.id,'title':item.title,'scheduled_start':stamp(item.scheduled_start),'duration_minutes':item.estimated_minutes or 0}for item in conflicts],'suggestions':[stamp(value)for value in suggestions]})
    @app.post('/api/schedule/apply')
    @jwt_required()
    def schedule_apply():
        entries=(request.get_json(silent=True)or{}).get('schedule')
        if not isinstance(entries,list)or len(entries)>1000:return fail('Schedule must contain at most 1000 task entries.')
        owner_id=user().id;proposed=[];seen=set()
        try:
            for entry in entries:
                if not isinstance(entry,dict):raise ValueError('Every schedule entry must be an object.')
                task_id=int(entry.get('task_id'));start=date(entry.get('suggested_start'))
                if not start or task_id in seen:raise ValueError('Schedule entries need unique task IDs and valid start times.')
                task=Task.query.filter_by(id=task_id,user_id=owner_id).first()
                if not task or task.status=='completed':raise ValueError('Schedule contains a missing or completed task.')
                seen.add(task_id);proposed.append((task,start,max(0,task.estimated_minutes or 0)))
        except (ValueError,TypeError)as e:return fail(str(e))
        proposed.sort(key=lambda item:item[1])
        for index,(task,start,duration)in enumerate(proposed):
            if schedule_conflicts(owner_id,start,duration,seen):return fail('Schedule overlaps an already scheduled task.',409)
            if index and proposed[index-1][1]+timedelta(minutes=proposed[index-1][2])>start:return fail('Schedule entries overlap each other.',409)
        for task,start,_ in proposed:task.scheduled_start=start
        db.session.commit()
        return jsonify({'scheduled_tasks':[task_data(task)for task,_,_ in proposed]})
    @app.post('/api/ai/parse-task')
    @jwt_required()
    @limiter.limit('20/hour')
    def parse_task():
        prompt=str((request.get_json(silent=True)or{}).get('prompt','')).strip()
        if not prompt or len(prompt)>2000:return fail('Task instructions are required and limited to 2000 characters.')
        try:data=ai('Extract one task from the user instruction. Return JSON only: {"task":{"title":string,"description":string,"priority":"low|medium|high|urgent","due_date":ISO-8601|null,"estimated_minutes":integer,"category":string,"tags":[string],"subtasks":[{"title":string}]}}. Resolve relative dates from the supplied current UTC time. Do not invent a deadline if none is given. Never save the task.',json.dumps({'current_utc':stamp(now()),'instruction':prompt}))
        except RuntimeError as e:return fail(str(e),503)
        raw=data.get('task')
        if not isinstance(raw,dict):return fail('AI response failed validation.',502)
        task=Task(user_id=user().id,priority='medium',category='General',estimated_minutes=30)
        try:update(task,raw)
        except (ValueError,TypeError)as e:return fail('AI returned an invalid task: '+str(e),502)
        if not isinstance(raw.get('subtasks',[]),list):return fail('AI returned invalid subtasks.',502)
        return jsonify({'task':{'title':task.title,'description':task.description,'priority':task.priority,'due_date':stamp(task.due_date),'estimated_minutes':task.estimated_minutes,'category':task.category,'tags':task.tags or [],'subtasks':[{'title':str(s.get('title','')).strip()[:200]}for s in raw.get('subtasks',[])[:20]if isinstance(s,dict)and str(s.get('title','')).strip()]}})
    @app.post('/api/ai/breakdown')
    @jwt_required()
    @limiter.limit('20/hour')
    def breakdown():
        d=request.get_json(silent=True)or{}
        try:t=mine(int(d.get('task_id')))
        except (TypeError,ValueError):return fail('A valid task_id is required.')
        if t.status=='completed':return fail('Completed tasks cannot be broken down.')
        try:data=ai('Break the task into 2-10 clear, actionable subtasks. Return JSON only: {"subtasks":[{"title":string}]}. Do not include explanations.',json.dumps({'title':t.title,'description':t.description,'existing_subtasks':[s.title for s in t.subtasks]}))
        except RuntimeError as e:return fail(str(e),503)
        raw=data.get('subtasks')
        if not isinstance(raw,list):return fail('AI response failed validation.',502)
        subtasks=[{'title':str(s.get('title','')).strip()[:200]}for s in raw[:10]if isinstance(s,dict)and str(s.get('title','')).strip()]
        if not 2<=len(subtasks)<=10:return fail('AI returned an invalid breakdown.',502)
        return jsonify({'task_id':t.id,'subtasks':subtasks})
    @app.post('/api/ai/prioritize')
    @jwt_required()
    @limiter.limit('20/hour')
    def prioritize():
        pending=[t for t in Task.query.filter_by(user_id=user().id).all()if t.status!='completed']
        if not pending:return jsonify({'recommendation':None})
        context=[{'id':t.id,'title':t.title,'priority':t.priority,'due_date':stamp(t.due_date),'estimated_minutes':t.estimated_minutes,'status':t.status}for t in pending]
        try:data=ai('Choose the single best task for the user to work on next. Consider urgency, due date, and task size. Return JSON only: {"task_id":integer,"reason":string}. task_id must be one of the supplied IDs.',json.dumps({'current_utc':stamp(now()),'tasks':context}))
        except RuntimeError as e:return fail(str(e),503)
        try:task_id=int(data.get('task_id'))
        except (TypeError,ValueError):return fail('AI returned an invalid task recommendation.',502)
        selected=next((t for t in pending if t.id==task_id),None)
        if not selected:return fail('AI recommended a task outside your task list.',502)
        return jsonify({'recommendation':{'task_id':selected.id,'title':selected.title,'reason':str(data.get('reason',''))[:1000]}})
    @app.get('/api/ai/briefing')
    @jwt_required()
    @limiter.limit('20/hour')
    def briefing():
        current=now();today_start=current.replace(hour=0,minute=0,second=0,microsecond=0);tomorrow=today_start+timedelta(days=1)
        tasks=[t for t in Task.query.filter_by(user_id=user().id).all()if t.status!='completed']
        context=[{'id':t.id,'title':t.title,'priority':t.priority,'status':t.status,'due_date':stamp(t.due_date),'estimated_minutes':t.estimated_minutes}for t in tasks]
        try:data=ai('Write a concise, useful daily task briefing based only on the supplied tasks. Mention due or overdue work and recommend a practical first step. Return JSON only: {"briefing":string}.',json.dumps({'current_utc':stamp(current),'today_start_utc':stamp(today_start),'tomorrow_utc':stamp(tomorrow),'tasks':context}))
        except RuntimeError as e:return fail(str(e),503)
        text=data.get('briefing')
        if not isinstance(text,str)or not text.strip():return fail('AI returned an invalid daily briefing.',502)
        return jsonify({'briefing':text.strip()[:4000],'pending_tasks':len(tasks),'due_today':sum(bool(t.due_date and today_start<=t.due_date<tomorrow)for t in tasks),'overdue':sum(bool(t.due_date and t.due_date<current)for t in tasks)})
    @app.route('/api/preferences',methods=['GET','PUT'])
    @jwt_required()
    def preferences():
        u=user()
        if request.method=='GET':return jsonify({'preferences':u.preferences or {}})
        values=request.get_json(silent=True)
        if not isinstance(values,dict):return fail('Preferences must be a JSON object.')
        if 'auth_version'in values:return fail('auth_version is managed by the authentication service.')
        prefs={**(u.preferences or{}),**values}
        for key in ('work_start_hour','work_end_hour'):
            if key in values and(type(values[key])is not int or not 0<=values[key]<=24):return fail(f'{key} must be an integer from 0 to 24.')
        if 'work_start_hour'in prefs and 'work_end_hour'in prefs and prefs['work_start_hour']>=prefs['work_end_hour']:return fail('work_start_hour must be earlier than work_end_hour.')
        for key,maximum in (('daily_goal_tasks',100),('weekly_goal_tasks',500)):
            if key in values and(type(values[key])is not int or not 1<=values[key]<=maximum):return fail(f'{key} must be an integer from 1 to {maximum}.')
        if 'notifications_enabled'in values and type(values['notifications_enabled'])is not bool:return fail('notifications_enabled must be true or false.')
        if 'theme'in values and values['theme']not in {'light','dark','system'}:return fail('theme must be light, dark, or system.')
        u.preferences=prefs;db.session.commit();return jsonify({'preferences':u.preferences})
    @app.get('/api/notifications')
    @jwt_required()
    def notifications():
        u=user();current=now();prefs=u.preferences or{}
        if prefs.get('notifications_enabled',True):
            open_query=Task.query.filter(Task.user_id==u.id,Task.status!='completed');open_count=open_query.count()
            if open_count:
                due_count=open_query.filter(Task.due_date>current,Task.due_date<=current+timedelta(days=1)).count();overdue_count=open_query.filter(Task.due_date.isnot(None),Task.due_date<=current).count();summary_key=f'daily-summary:{u.id}:{current.date().isoformat()}'
                summary_created=not bool(Notification.query.filter_by(dedupe_key=summary_key).first())
                if summary_created:notify(u.id,'Daily task summary',f'{open_count} open task{""if open_count==1 else"s"} · {due_count} due within 24 hours · {overdue_count} overdue.','daily-summary',dedupe_key=summary_key)
            else:summary_created=False
            due_tasks=Task.query.filter(Task.user_id==u.id,Task.status!='completed',Task.due_date.isnot(None),Task.due_date<=current+timedelta(days=1)).order_by(Task.due_date.asc()).limit(500).all()
            pending=[]
            for task in due_tasks:
                overdue=task.due_date<=current;kind='overdue'if overdue else'due-soon';key=f'{kind}:{u.id}:{task.id}:{current.date().isoformat()}'
                pending.append((key,task,overdue))
            keys=[item[0]for item in pending]
            existing={row[0]for row in db.session.query(Notification.dedupe_key).filter(Notification.dedupe_key.in_(keys)).all()}if keys else set()
            created=0
            for key,task,overdue in pending:
                if key not in existing:
                    notify(u.id,'Task overdue'if overdue else'Task due soon',f'“{task.title}” is past its deadline.'if overdue else f'“{task.title}” is due within 24 hours.','overdue'if overdue else'due-soon',task.id,key);created+=1
            if summary_created or created:db.session.commit()
        query=Notification.query.filter_by(user_id=u.id)
        if request.args.get('unread')=='true':query=query.filter(Notification.read_at.is_(None))
        records=query.order_by(Notification.created_at.desc(),Notification.id.desc()).limit(100).all()
        return jsonify({'notifications':[{'id':n.id,'title':n.title,'message':n.message,'kind':n.kind,'task_id':n.task_id,'created_at':stamp(n.created_at),'read_at':stamp(n.read_at)}for n in records],'unread_count':Notification.query.filter_by(user_id=u.id,read_at=None).count()})
    @app.patch('/api/notifications/<int:notification_id>/read')
    @jwt_required()
    def notification_read(notification_id):
        item=Notification.query.filter_by(id=notification_id,user_id=user().id).first_or_404()
        if item.read_at is None:item.read_at=now();db.session.commit()
        return jsonify({'notification_id':item.id,'read_at':stamp(item.read_at)})
    @app.post('/api/notifications/read-all')
    @jwt_required()
    def notifications_read_all():
        count=Notification.query.filter_by(user_id=user().id,read_at=None).update({'read_at':now()},synchronize_session=False);db.session.commit()
        return jsonify({'updated':count})
    @app.get('/api/openapi.json')
    def openapi():
        bearer=[{'bearerAuth':[]}];json_body=lambda example:{'required':True,'content':{'application/json':{'schema':{'type':'object'},'example':example}}}
        return jsonify({'openapi':'3.0.3','info':{'title':'TaskMaster Pro API','version':'1.0.0','description':'Use Register or Login, copy `access_token`, then click Authorize and enter `Bearer <token>`.'},'components':{'securitySchemes':{'bearerAuth':{'type':'http','scheme':'bearer','bearerFormat':'JWT'}},'schemas':{'Error':{'type':'object','properties':{'error':{'type':'object','properties':{'message':{'type':'string'}}}}}}},'paths':{
            '/api/health':{'get':{'summary':'Backend health check','responses':{'200':{'description':'Running'}}}},
            '/api/auth/register':{'post':{'summary':'Register a user','requestBody':json_body({'name':'Alex','email':'alex@example.com','password':'Password1!'}),'responses':{'201':{'description':'JWT and user'}}}},
            '/api/auth/login':{'post':{'summary':'Log in','requestBody':json_body({'email':'alex@example.com','password':'Password1!'}),'responses':{'200':{'description':'JWT and user'},'401':{'description':'Invalid credentials'}}}},
            '/api/auth/me':{'get':{'summary':'Current authenticated user','security':bearer,'responses':{'200':{'description':'User'}}}},
            '/api/tasks':{'get':{'summary':'List your tasks','security':bearer,'parameters':[{'name':'status','in':'query','schema':{'type':'string','enum':['todo','in_progress','completed']}},{'name':'priority','in':'query','schema':{'type':'string','enum':['low','medium','high','urgent']}}],'responses':{'200':{'description':'Tasks'}}},'post':{'summary':'Create a task','security':bearer,'requestBody':json_body({'title':'Finish API tests','description':'Test with Swagger UI','priority':'high','category':'Work','estimated_minutes':45,'due_date':'2026-10-20T17:00:00+00:00','tags':['api']}),'responses':{'201':{'description':'Created task'}}}},
            '/api/tasks/{task_id}':{'get':{'summary':'Get an owned task','security':bearer,'parameters':[{'name':'task_id','in':'path','required':True,'schema':{'type':'integer'}}],'responses':{'200':{'description':'Task'}}},'put':{'summary':'Update an owned task','security':bearer,'parameters':[{'name':'task_id','in':'path','required':True,'schema':{'type':'integer'}}],'requestBody':json_body({'title':'Updated task','priority':'medium'}),'responses':{'200':{'description':'Updated task'}}},'delete':{'summary':'Delete an owned task','security':bearer,'parameters':[{'name':'task_id','in':'path','required':True,'schema':{'type':'integer'}}],'responses':{'204':{'description':'Deleted'}}}},
            '/api/tasks/{task_id}/status':{'patch':{'summary':'Change task status','security':bearer,'parameters':[{'name':'task_id','in':'path','required':True,'schema':{'type':'integer'}}],'requestBody':json_body({'status':'completed'}),'responses':{'200':{'description':'Updated task'}}}},
            '/api/analytics/overview':{'get':{'summary':'Your productivity summary','security':bearer,'responses':{'200':{'description':'Calculated analytics'}}}},
            '/api/analytics/trends':{'get':{'summary':'Seven-day completion trend','security':bearer,'responses':{'200':{'description':'Trend data'}}}},
            '/api/focus-sessions':{'post':{'summary':'Record a focus session','security':bearer,'requestBody':json_body({'start_time':'2026-10-18T09:00:00+00:00','end_time':'2026-10-18T09:25:00+00:00'}),'responses':{'201':{'description':'Saved session'}}}},
            '/api/ai/generate-tasks':{'post':{'summary':'Generate an editable AI task plan','security':bearer,'requestBody':json_body({'prompt':'Prepare for my Python interview in seven days. I can study two hours daily.'}),'responses':{'200':{'description':'Preview only; no tasks are saved'},'503':{'description':'AI is not configured or unavailable'}}}},
            '/api/ai/chat':{'post':{'summary':'Ask the task-aware AI assistant','security':bearer,'requestBody':json_body({'message':'What should I work on today?'}),'responses':{'200':{'description':'AI response'},'503':{'description':'AI is unavailable'}}}},
            '/api/ai/schedule':{'post':{'summary':'Create a deterministic schedule preview','security':bearer,'requestBody':json_body({'start_hour':9,'end_hour':17}),'responses':{'200':{'description':'Review-only scheduling proposal'}}}},
            '/api/preferences':{'get':{'summary':'Get user preferences','security':bearer,'responses':{'200':{'description':'Preferences'}}},'put':{'summary':'Update preferences','security':bearer,'requestBody':json_body({'work_start_hour':9,'work_end_hour':17}),'responses':{'200':{'description':'Preferences'}}}}
        }})
    with app.app_context():db.create_all()
    return app
