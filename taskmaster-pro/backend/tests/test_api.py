import pytest
from io import BytesIO
from datetime import datetime
import re
import app as app_module
from app import create_app,db
@pytest.fixture
def client():
    app=create_app({'TESTING':True,'SQLALCHEMY_DATABASE_URI':'sqlite:///:memory:','JWT_SECRET_KEY':'test'})
    with app.app_context():db.drop_all();db.create_all()
    return app.test_client()
def header(client,email='a@example.com'):
    r=client.post('/api/auth/register',json={'name':'A','email':email,'password':'Password1!'})
    return {'Authorization':'Bearer '+r.get_json()['access_token']}
def test_auth_and_isolation(client):
    h=header(client);assert client.get('/api/auth/me',headers=h).status_code==200
    task=client.post('/api/tasks',headers=h,json={'title':'Private'}).get_json()['task']
    assert client.get('/api/tasks/'+str(task['id']),headers=header(client,'b@example.com')).status_code==404

def test_password_reset_uses_one_time_email_tokens_and_revokes_old_access(client,monkeypatch):
    original=header(client,'reset@example.com')
    monkeypatch.delenv('SMTP_HOST',raising=False);monkeypatch.delenv('SMTP_FROM',raising=False)
    assert client.post('/api/auth/password-reset/request',json={'email':'reset@example.com'}).status_code==503
    monkeypatch.setenv('SMTP_HOST','mail.example.test');monkeypatch.setenv('SMTP_FROM','support@example.test');monkeypatch.setenv('SMTP_USE_TLS','false')
    sent=[]
    class FakeSMTP:
        def __init__(self,*args,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def send_message(self,message):sent.append(message)
    monkeypatch.setattr(app_module.smtplib,'SMTP',FakeSMTP)
    assert client.post('/api/auth/password-reset/request',json={'email':'unknown@example.com'}).status_code==202
    requested=client.post('/api/auth/password-reset/request',json={'email':'reset@example.com'})
    assert requested.status_code==202 and len(sent)==1
    token=re.search(r'reset_token=([A-Za-z0-9_-]+)',sent[0].get_content()).group(1)
    changed=client.post('/api/auth/password-reset/confirm',json={'token':token,'password':'NewPassword1!'})
    assert changed.status_code==200
    assert client.get('/api/auth/me',headers=original).status_code==401
    login=client.post('/api/auth/login',json={'email':'reset@example.com','password':'NewPassword1!'})
    assert login.status_code==200
    fresh={'Authorization':'Bearer '+login.get_json()['access_token']}
    assert client.get('/api/auth/me',headers=fresh).status_code==200
    assert client.post('/api/auth/password-reset/confirm',json={'token':token,'password':'AnotherPass1!'}).status_code==400
def test_status_and_analytics(client):
    h=header(client);tid=client.post('/api/tasks',headers=h,json={'title':'Plan'}).get_json()['task']['id']
    assert client.patch('/api/tasks/'+str(tid)+'/status',headers=h,json={'status':'completed'}).status_code==200
    assert client.get('/api/analytics/overview',headers=h).get_json()['completed_tasks']==1
def test_analytics_with_due_date(client):
    h=header(client)
    assert client.post('/api/tasks',headers=h,json={'title':'Dated task','due_date':'2026-10-20T12:00:00+00:00'}).status_code==201
    assert client.get('/api/analytics/overview',headers=h).status_code==200

def test_offline_task_request_is_idempotent_and_scoped(client):
    h=header(client)
    first=client.post('/api/tasks',headers=h,json={'title':'Saved offline','client_request_id':'offline-request-1'})
    repeated=client.post('/api/tasks',headers=h,json={'title':'Should not duplicate','client_request_id':'offline-request-1'})
    assert first.status_code==201
    assert repeated.status_code==200
    assert repeated.get_json()['duplicate_request'] is True
    assert repeated.get_json()['task']['id']==first.get_json()['task']['id']
    assert len(client.get('/api/tasks',headers=h).get_json()['tasks'])==1
    other=header(client,'offline-other@example.com')
    assert client.post('/api/tasks',headers=other,json={'title':'Separate account','client_request_id':'offline-request-1'}).status_code==201

def test_calendar_ics_export_escapes_content_and_is_user_scoped(client):
    h=header(client)
    client.post('/api/tasks',headers=h,json={'title':'Plan, review','description':'First line\nSecond line','due_date':'2026-10-20T12:00:00Z','estimated_minutes':30})
    response=client.get('/api/export/calendar.ics',headers=h)
    assert response.status_code==200
    assert response.mimetype=='text/calendar'
    assert 'SUMMARY:Plan\\, review' in response.get_data(as_text=True)
    assert 'DTSTART:20261020T113000Z' in response.get_data(as_text=True)
    assert 'DESCRIPTION:First line\\nSecond line\\nDeadline:' in response.get_data(as_text=True)
    assert 'Plan' not in client.get('/api/export/calendar.ics',headers=header(client,'calendar-other@example.com')).get_data(as_text=True)

def test_notifications_persist_due_reminders_and_enforce_ownership(client,monkeypatch):
    monkeypatch.setattr(app_module,'now',lambda:datetime(2026,10,18,9,0))
    h=header(client)
    task=client.post('/api/tasks',headers=h,json={'title':'Submit proposal','due_date':'2026-10-18T12:00:00Z'}).get_json()['task']
    first=client.get('/api/notifications',headers=h).get_json()
    second=client.get('/api/notifications',headers=h).get_json()
    assert first['unread_count']==second['unread_count']
    assert sum(item['kind']=='due-soon'for item in first['notifications'])==1
    assert sum(item['kind']=='daily-summary'for item in first['notifications'])==1
    reminder=next(item for item in first['notifications']if item['kind']=='due-soon')
    assert client.patch(f"/api/notifications/{reminder['id']}/read",headers=h).status_code==200
    assert client.get('/api/notifications?unread=true',headers=h).get_json()['unread_count']==first['unread_count']-1
    other=header(client,'notifications@example.com')
    assert client.patch(f"/api/notifications/{reminder['id']}/read",headers=other).status_code==404
    assert client.patch(f"/api/tasks/{task['id']}/status",headers=h,json={'status':'completed'}).status_code==200
    notices=client.get('/api/notifications',headers=h).get_json()['notifications']
    assert any(item['kind']=='completion'for item in notices)

def test_focus_history_break_sessions_and_productivity_preferences(client,monkeypatch):
    monkeypatch.setattr(app_module,'now',lambda:datetime(2026,10,18,9,0))
    h=header(client);task=client.post('/api/tasks',headers=h,json={'title':'Write report'}).get_json()['task']
    response=client.post('/api/focus-sessions',headers=h,json={'task_id':task['id'],'start_time':'2026-10-18T08:00:00Z','end_time':'2026-10-18T08:25:00Z'})
    assert response.status_code==201
    assert client.post('/api/focus-sessions',headers=h,json={'session_type':'break','start_time':'2026-10-18T08:25:00Z','end_time':'2026-10-18T08:30:00Z'}).status_code==201
    history=client.get('/api/focus-sessions',headers=h).get_json()
    assert history['stats']=={'focus_minutes_today':25,'break_minutes_today':5,'focus_minutes_week':25,'sessions_total':2}
    assert {session['session_type']for session in history['sessions']}=={'focus','break'}
    assert client.get(f"/api/tasks/{task['id']}",headers=h).get_json()['task']['actual_minutes']==25
    assert client.put('/api/preferences',headers=h,json={'daily_goal_tasks':4,'weekly_goal_tasks':20,'notifications_enabled':False}).status_code==200
    assert client.get('/api/analytics/overview',headers=h).get_json()['goals']['daily']['target']==4
    assert client.put('/api/preferences',headers=h,json={'daily_goal_tasks':0}).status_code==400
    assert client.put('/api/preferences',headers=h,json={'work_start_hour':18,'work_end_hour':9}).status_code==400

def test_scheduler_respects_working_hours_and_reports_oversized_tasks(client,monkeypatch):
    monkeypatch.setattr(app_module,'now',lambda:datetime(2026,10,18,16,0))
    h=header(client)
    client.post('/api/tasks',headers=h,json={'title':'Urgent task','priority':'urgent','estimated_minutes':90})
    client.post('/api/tasks',headers=h,json={'title':'Long task','estimated_minutes':600})
    client.post('/api/tasks',headers=h,json={'title':'Completed task','status':'completed'})

    response=client.post('/api/ai/schedule',headers=h,json={})
    assert response.status_code==200
    data=response.get_json()
    assert [(task['title'],task['suggested_start']) for task in data['schedule']]==[
        ('Urgent task','2026-10-19T09:00:00Z'),
    ]
    assert data['unscheduled']==[{
        'task_id':2,
        'title':'Long task',
        'reason':'Estimated duration exceeds the configured workday.',
    }]
    assert client.post('/api/ai/schedule',headers=h,json={'start_hour':'invalid'}).status_code==400

def test_scheduler_avoids_busy_slots_detects_conflicts_and_applies_preview(client,monkeypatch):
    monkeypatch.setattr(app_module,'now',lambda:datetime(2026,10,18,9,0))
    h=header(client)
    fixed=client.post('/api/tasks',headers=h,json={'title':'Fixed meeting','scheduled_start':'2026-10-18T10:00:00Z','estimated_minutes':60}).get_json()['task']
    pending=client.post('/api/tasks',headers=h,json={'title':'Work block','estimated_minutes':120,'priority':'urgent'}).get_json()['task']

    preview=client.post('/api/ai/schedule',headers=h,json={}).get_json()['schedule']
    assert [(item['task_id'],item['suggested_start'])for item in preview]==[(pending['id'],'2026-10-18T11:00:00Z')]
    conflicts=client.post('/api/calendar/conflicts',headers=h,json={'task_id':pending['id'],'scheduled_start':'2026-10-18T10:30:00Z','duration_minutes':30}).get_json()
    assert [item['title']for item in conflicts['conflicts']]==['Fixed meeting']
    assert conflicts['suggestions']
    rejected=client.put(f"/api/tasks/{pending['id']}",headers=h,json={'scheduled_start':'2026-10-18T10:30:00Z'})
    assert rejected.status_code==409
    applied=client.post('/api/schedule/apply',headers=h,json={'schedule':preview})
    assert applied.status_code==200
    assert client.get(f"/api/tasks/{pending['id']}",headers=h).get_json()['task']['scheduled_start']=='2026-10-18T11:00:00Z'
    overlapping=[{'task_id':fixed['id'],'suggested_start':'2026-10-18T12:00:00Z'},{'task_id':pending['id'],'suggested_start':'2026-10-18T12:30:00Z'}]
    assert client.post('/api/schedule/apply',headers=h,json={'schedule':overlapping}).status_code==409

def test_ai_parse_task_returns_validated_preview_without_saving(client,monkeypatch):
    h=header(client)
    monkeypatch.setattr(app_module,'ai',lambda *_:{'task':{'title':'Submit report','priority':'high','due_date':'2026-10-20T17:00:00Z','subtasks':[{'title':'Review data'}]}})

    response=client.post('/api/ai/parse-task',headers=h,json={'prompt':'Submit my report by Tuesday at 5 PM'})
    assert response.status_code==200
    task=response.get_json()['task']
    assert task['title']=='Submit report'
    assert task['priority']=='high'
    assert task['estimated_minutes']==30
    assert task['subtasks']==[{'title':'Review data'}]
    assert client.get('/api/tasks',headers=h).get_json()['tasks']==[]

def test_ai_breakdown_recommendation_and_briefing_are_user_scoped(client,monkeypatch):
    h=header(client)
    task=client.post('/api/tasks',headers=h,json={'title':'Prepare launch','due_date':'2026-10-19T10:00:00Z'}).get_json()['task']
    def mocked_ai(system,prompt):
        if 'Break the task' in system:return {'subtasks':[{'title':'Draft plan'},{'title':'Review plan'}]}
        if 'Choose the single best task' in system:return {'task_id':task['id'],'reason':'It has the nearest deadline.'}
        return {'briefing':'Start by drafting the plan.'}
    monkeypatch.setattr(app_module,'ai',mocked_ai)

    breakdown=client.post('/api/ai/breakdown',headers=h,json={'task_id':task['id']})
    assert breakdown.status_code==200
    assert len(breakdown.get_json()['subtasks'])==2
    assert client.post('/api/ai/breakdown',headers=header(client,'other@example.com'),json={'task_id':task['id']}).status_code==404
    recommendation=client.post('/api/ai/prioritize',headers=h,json={})
    assert recommendation.get_json()['recommendation']['task_id']==task['id']
    briefing=client.get('/api/ai/briefing',headers=h)
    assert briefing.get_json()['briefing']=='Start by drafting the plan.'

def test_ai_generated_subtasks_are_included_and_persist_when_approved(client,monkeypatch):
    h=header(client)
    monkeypatch.setattr(app_module,'ai',lambda *_:{'tasks':[{'title':'Plan launch','subtasks':[{'title':'Write checklist'},{'title':'Review checklist'}]}]})

    generated=client.post('/api/ai/generate-tasks',headers=h,json={'prompt':'Plan a launch'}).get_json()['tasks'][0]
    assert [step['title'] for step in generated['subtasks']]==['Write checklist','Review checklist']
    created=client.post('/api/tasks',headers=h,json=generated)
    assert created.status_code==201,created.get_json()
    assert [step['title'] for step in created.get_json()['task']['subtasks']]==['Write checklist','Review checklist']

def test_bulk_subtask_create_validates_all_items_before_writing(client):
    h=header(client)
    task_id=client.post('/api/tasks',headers=h,json={'title':'Parent'}).get_json()['task']['id']

    invalid=client.post(f'/api/tasks/{task_id}/subtasks/bulk',headers=h,json={'subtasks':[{'title':'Valid'},{'title':' '}]})
    assert invalid.status_code==400
    assert client.get(f'/api/tasks/{task_id}',headers=h).get_json()['task']['subtasks']==[]
    valid=client.post(f'/api/tasks/{task_id}/subtasks/bulk',headers=h,json={'subtasks':[{'title':'Step one'},{'title':'Step two'}]})
    assert valid.status_code==201
    assert len(valid.get_json()['subtasks'])==2

def test_task_search_matches_tags_and_respects_user_scope(client):
    h=header(client)
    client.post('/api/tasks',headers=h,json={'title':'Quarterly plan','tags':['finance','review']})
    client.post('/api/tasks',headers=h,json={'title':'Clean desk'})
    assert [t['title']for t in client.get('/api/tasks?q=finance',headers=h).get_json()['tasks']]==['Quarterly plan']
    other=header(client,'other@example.com')
    assert client.get('/api/tasks?q=finance',headers=other).get_json()['tasks']==[]
    assert client.get('/api/tasks?q='+('x'*201),headers=h).status_code==400

def test_task_dependencies_prevent_cycles_and_cross_user_links(client):
    h=header(client)
    first=client.post('/api/tasks',headers=h,json={'title':'First'}).get_json()['task']
    second=client.post('/api/tasks',headers=h,json={'title':'Second'}).get_json()['task']
    third=client.post('/api/tasks',headers=h,json={'title':'Third'}).get_json()['task']
    assert client.put(f"/api/tasks/{first['id']}/dependencies",headers=h,json={'depends_on_ids':[second['id']]}).status_code==200
    assert client.put(f"/api/tasks/{second['id']}/dependencies",headers=h,json={'depends_on_ids':[third['id']]}).status_code==200
    cycle=client.put(f"/api/tasks/{third['id']}/dependencies",headers=h,json={'depends_on_ids':[first['id']]})
    assert cycle.status_code==400
    assert client.get(f"/api/tasks/{first['id']}",headers=h).get_json()['task']['dependencies']==[second['id']]
    other=header(client,'other@example.com')
    assert client.put(f"/api/tasks/{first['id']}/dependencies",headers=other,json={'depends_on_ids':[second['id']]}).status_code==404
    assert client.delete(f"/api/tasks/{second['id']}",headers=h).status_code==204
    assert client.get(f"/api/tasks/{first['id']}",headers=h).get_json()['task']['dependencies']==[]

def test_export_and_restore_backup_preserve_owned_task_data(client):
    source=header(client)
    first=client.post('/api/tasks',headers=source,json={'title':'Prepare','tags':['launch'],'subtasks':[{'title':'Draft'}]}).get_json()['task']
    second=client.post('/api/tasks',headers=source,json={'title':'Review'}).get_json()['task']
    client.put(f"/api/tasks/{second['id']}/dependencies",headers=source,json={'depends_on_ids':[first['id']]})
    project=client.post('/api/projects',headers=source,json={'name':'Launch'}).get_json()['project']
    client.put(f"/api/projects/{project['id']}/tasks",headers=source,json={'task_ids':[first['id']]})
    client.post(f"/api/projects/{project['id']}/milestones",headers=source,json={'title':'Release'})
    client.put('/api/preferences',headers=source,json={'work_start_hour':8})
    client.post('/api/focus-sessions',headers=source,json={'start_time':'2026-10-18T08:00:00Z','end_time':'2026-10-18T08:25:00Z','task_id':first['id']})
    client.post('/api/focus-sessions',headers=source,json={'session_type':'break','start_time':'2026-10-18T08:25:00Z','end_time':'2026-10-18T08:30:00Z'})

    csv_response=client.get('/api/export/tasks.csv',headers=source)
    assert csv_response.status_code==200
    assert 'taskmaster-tasks.csv' in csv_response.headers['Content-Disposition']
    backup=client.get('/api/export/backup',headers=source).get_json()
    assert len(backup['tasks'])==2
    assert backup['focus_sessions'][0]['duration_minutes']==25
    assert backup['focus_sessions'][1]['session_type']=='break'
    assert backup['preferences']['work_start_hour']==8
    assert backup['projects'][0]['name']=='Launch'
    destination=header(client,'restore@example.com')
    restored=client.post('/api/import/backup',headers=destination,json=backup)
    assert restored.status_code==201,restored.get_json()
    restored_tasks=client.get('/api/tasks',headers=destination).get_json()['tasks']
    assert len(restored_tasks)==2
    assert len(next(t for t in restored_tasks if t['title']=='Review')['dependencies'])==1
    assert next(t for t in restored_tasks if t['title']=='Prepare')['project_ids']
    assert client.get('/api/preferences',headers=destination).get_json()['preferences']['work_start_hour']==8
    assert len(client.get('/api/focus-sessions',headers=destination).get_json()['sessions'])==2
    assert client.get('/api/projects',headers=destination).get_json()['projects'][0]['milestones'][0]['title']=='Release'

def test_projects_group_tasks_track_milestones_and_remain_user_scoped(client):
    h=header(client)
    project=client.post('/api/projects',headers=h,json={'name':'Website','description':'Launch work'}).get_json()['project']
    task=client.post('/api/tasks',headers=h,json={'title':'Build page','project_ids':[project['id']]}).get_json()['task']
    assert task['projects']==[{'id':project['id'],'name':'Website'}]
    milestone=client.post(f"/api/projects/{project['id']}/milestones",headers=h,json={'title':'First review','due_date':'2026-10-20T12:00:00Z'}).get_json()['milestone']
    assert milestone['completed'] is False
    client.patch(f"/api/projects/{project['id']}/milestones/{milestone['id']}",headers=h,json={'completed':True})
    refreshed=client.get('/api/projects',headers=h).get_json()['projects'][0]
    assert refreshed['task_count']==1
    assert refreshed['progress_percentage']==0
    assert refreshed['milestones'][0]['completed'] is True
    other=header(client,'other@example.com')
    assert client.get(f"/api/projects/{project['id']}",headers=other).status_code==404
    assert client.delete(f"/api/projects/{project['id']}",headers=h).status_code==204
    assert client.get(f"/api/tasks/{task['id']}",headers=h).get_json()['task']['project_ids']==[]

def test_project_collaboration_comments_assignments_and_scoped_updates(client):
    owner=header(client,'owner@example.com');member=header(client,'member@example.com');outsider=header(client,'outsider@example.com')
    project=client.post('/api/projects',headers=owner,json={'name':'Shared launch'}).get_json()['project']
    task=client.post('/api/tasks',headers=owner,json={'title':'Review launch plan','project_ids':[project['id']]}).get_json()['task']
    invited=client.post(f"/api/projects/{project['id']}/members",headers=owner,json={'email':'member@example.com'})
    assert invited.status_code==201
    assert client.post(f"/api/projects/{project['id']}/members",headers=owner,json={'email':'member@example.com'}).status_code==409
    assert client.put(f"/api/projects/{project['id']}/assignments",headers=owner,json={'assignments':[{'task_id':task['id'],'user_id':invited.get_json()['member']['id']}]}).status_code==200
    shared=client.get('/api/shared/projects',headers=member).get_json()['projects'][0]
    assert shared['project']['name']=='Shared launch'
    assert shared['tasks'][0]['assigned_to_me'] is True
    assert client.post(f"/api/projects/{project['id']}/comments",headers=member,json={'message':'I will review this today.'}).status_code==201
    assert client.get(f"/api/projects/{project['id']}/comments",headers=outsider).status_code==404
    assignment=client.get('/api/assigned-tasks',headers=member).get_json()['assignments'][0]
    assert assignment['task']['id']==task['id']
    assert client.patch(f"/api/assigned-tasks/{task['id']}/status",headers=member,json={'status':'completed'}).status_code==200
    assert client.get(f"/api/tasks/{task['id']}",headers=owner).get_json()['task']['status']=='completed'
    assert client.get(f"/api/tasks/{task['id']}",headers=member).status_code==404
    assert any(row['kind']=='collaboration'for row in client.get('/api/notifications',headers=owner).get_json()['notifications'])
    assert client.delete(f"/api/projects/{project['id']}/members/{invited.get_json()['member']['id']}",headers=owner).status_code==204
    assert client.get('/api/shared/projects',headers=member).get_json()['projects']==[]

def test_recurring_tasks_create_one_month_clamped_followup(client,monkeypatch):
    monkeypatch.setattr(app_module,'now',lambda:datetime(2026,10,18,9,0))
    h=header(client)
    task=client.post('/api/tasks',headers=h,json={'title':'Monthly report','due_date':'2026-10-31T12:00:00Z','recurrence':{'frequency':'monthly','interval':1}}).get_json()['task']
    assert task['recurrence']=={'frequency':'monthly','interval':1}
    completed=client.patch(f"/api/tasks/{task['id']}/status",headers=h,json={'status':'completed'})
    assert completed.status_code==200
    next_task=completed.get_json()['next_occurrence']
    assert next_task['due_date']=='2026-11-30T12:00:00Z'
    assert next_task['status']=='todo'
    assert next_task['recurrence']=={'frequency':'monthly','interval':1}
    assert 'next_occurrence'not in client.patch(f"/api/tasks/{task['id']}/status",headers=h,json={'status':'completed'}).get_json()
    assert client.post('/api/tasks',headers=h,json={'title':'Invalid','recurrence':{'frequency':'yearly'}}).status_code==400

def test_attachments_upload_download_delete_and_enforce_ownership(client,tmp_path):
    client.application.instance_path=str(tmp_path)
    h=header(client)
    task_id=client.post('/api/tasks',headers=h,json={'title':'Notes'}).get_json()['task']['id']
    uploaded=client.post(f'/api/tasks/{task_id}/attachments',headers=h,data={'file':(BytesIO(b'private notes'),'meeting.txt')},content_type='multipart/form-data')
    assert uploaded.status_code==201
    attachment=uploaded.get_json()['attachment']
    denied=client.post(f'/api/tasks/{task_id}/attachments',headers=h,data={'file':(BytesIO(b'executable'),'malware.exe')},content_type='multipart/form-data')
    assert denied.status_code==400
    listed=client.get(f'/api/tasks/{task_id}/attachments',headers=h).get_json()['attachments']
    assert listed[0]['filename']=='meeting.txt'
    downloaded=client.get(f"/api/attachments/{attachment['id']}",headers=h)
    assert downloaded.data==b'private notes'
    other=header(client,'other@example.com')
    assert client.get(f"/api/attachments/{attachment['id']}",headers=other).status_code==404
    stored_path=next((tmp_path/'uploads').iterdir())
    assert stored_path.exists()
    downloaded.close()
    assert client.delete(f"/api/attachments/{attachment['id']}",headers=h).status_code==204
    assert list((tmp_path/'uploads').iterdir())==[]
