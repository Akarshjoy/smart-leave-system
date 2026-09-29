import importlib
import json
import os
import sys
import time
import uuid
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'backend'))
from domain import days_between, validate_rule


@pytest.fixture
def system(monkeypatch):
    for k,v in {'AWS_DEFAULT_REGION':'us-east-1','AWS_ACCESS_KEY_ID':'testing','AWS_SECRET_ACCESS_KEY':'testing',
                'PEOPLE':'people','REQUESTS':'requests','BALANCES':'balances','CONFIG':'config','LOCKS':'locks',
                'PORTAL':'https://example.invalid','SENDER':'sender@example.com','HR_EMAIL':'hr@example.com',
                'MANAGER_TOPIC':'arn:aws:sns:us-east-1:123456789012:managers','API_BASE':'https://api.example.invalid','APPROVAL_SECONDS':'172800'}.items():
        monkeypatch.setenv(k,v)
    with mock_aws():
        db=boto3.resource('dynamodb')
        for name,pk,sk in [('people','employee_id',None),('requests','employee_id','request_id'),('balances','employee_id','balance_key'),('config','leave_type',None),('locks','employee_id','leave_date')]:
            keys=[(pk,'HASH')]+([(sk,'RANGE')] if sk else [])
            db.create_table(TableName=name,KeySchema=[{'AttributeName':k,'KeyType':t} for k,t in keys],AttributeDefinitions=[{'AttributeName':k,'AttributeType':'S'} for k,_ in keys],BillingMode='PAY_PER_REQUEST')
        secret=boto3.client('secretsmanager').create_secret(Name='signing',SecretString='test-secret'*8)
        monkeypatch.setenv('SIGNING_SECRET',secret['ARN'])
        import app
        a=importlib.reload(app)
        for eid,role,manager in [('employee','Employee','manager'),('manager','Manager',''),('hr','HRAdmin',''),('other','Manager','')]:
            db.Table('people').put_item(Item={'employee_id':eid,'name':eid,'email':eid+'@example.com','role':role,'manager_id':manager,'active':True})
        rule={'leave_type':'casual','annual_quota':12,'carry_cap':5,'unlimited':False,'working_days_only':True,'max_calendar_days':60,'enabled':True,'holidays':[],'version':'v1'}
        db.Table('config').put_item(Item=rule)
        yield a,rule


def payload(offset=3,span=1):
    d=date.today()+timedelta(days=offset)
    while d.weekday()>4: d+=timedelta(days=1)
    if (d+timedelta(days=span-1)).year != d.year:
        d=date(d.year+1,1,5)
    return {'request_id':str(uuid.uuid4()),'leave_type':'casual','start_date':d.isoformat(),'end_date':(d+timedelta(days=span-1)).isoformat(),'reason':'Family time'}


def approve(a,r,hr=False):
    k={'employee_id':r['employee_id'],'request_id':r['request_id']}
    a.table('REQUESTS').update_item(Key=k,UpdateExpression='SET manager_decision=:d',ExpressionAttributeValues={':d':'approve'})
    if hr:
        a.table('REQUESTS').update_item(Key=k,UpdateExpression='SET hr_decision=:d',ExpressionAttributeValues={':d':'approve'})
    return a.request(r['employee_id'],r['request_id'])


def test_working_days_and_holidays(system):
    _,rule=system
    rule['holidays']=['2027-01-04']
    dates,n=days_between('2027-01-01','2027-01-05',rule,date(2027,1,1))
    assert len(dates)==5 and n==2


def test_cross_year_rejected(system):
    with pytest.raises(ValueError,match='Split'):
        days_between('2026-12-31','2027-01-02',system[1],date(2026,1,1))


def test_no_weekday_rejected(system):
    with pytest.raises(ValueError,match='no chargeable'):
        days_between('2027-01-02','2027-01-03',system[1],date(2027,1,1))


def test_submit_does_not_debit_and_is_idempotent(system):
    a,_=system; p=payload(); r=a.submit(a.employee('employee'),p)
    assert a.balance('employee','casual',r['year'])['used']==0
    assert a.submit(a.employee('employee'),p)['request_id']==r['request_id']
    assert len(a.history('employee'))==1


def test_insufficient_auto_reject(system):
    a,_=system; p=payload(); b=a.balance('employee','casual',int(p['start_date'][:4]))
    a.table('BALANCES').update_item(Key={'employee_id':'employee','balance_key':b['balance_key']},UpdateExpression='SET used=:n',ExpressionAttributeValues={':n':12})
    r=a.submit(a.employee('employee'),p)
    assert r['status']=='REJECTED' and 'Insufficient' in r['outcome_reason']


def test_final_debit_exactly_once(system):
    a,_=system; r=approve(a,a.submit(a.employee('employee'),payload()))
    assert a.finalize(r,'APPROVED','ok')=='APPROVED'
    assert a.finalize(r,'APPROVED','retry')=='APPROVED'
    assert a.balance('employee','casual',r['year'])['used']==r['days']


def test_overlap_final_transaction(system):
    a,_=system;p=payload();r1=a.submit(a.employee('employee'),p);p['request_id']=str(uuid.uuid4());r2=a.submit(a.employee('employee'),p)
    a.finalize(approve(a,r1),'APPROVED','ok')
    assert a.finalize(approve(a,r2),'APPROVED','ok')=='REJECTED'
    assert a.balance('employee','casual',r1['year'])['used']==r1['days']


def test_overlap_submission(system):
    a,_=system;p=payload();r=a.submit(a.employee('employee'),p);a.finalize(approve(a,r),'APPROVED','ok')
    p['request_id']=str(uuid.uuid4());new=a.submit(a.employee('employee'),p)
    assert new['status']=='REJECTED' and 'overlap' in new['outcome_reason']


def test_pending_requests_cannot_overspend(system):
    a,_=system;a.table('CONFIG').update_item(Key={'leave_type':'casual'},UpdateExpression='SET annual_quota=:n',ExpressionAttributeValues={':n':1})
    x=a.submit(a.employee('employee'),payload(3));y=a.submit(a.employee('employee'),payload(14))
    a.finalize(approve(a,x),'APPROVED','ok')
    assert a.finalize(approve(a,y),'APPROVED','ok')=='REJECTED'
    assert a.balance('employee','casual',x['year'])['used']==1


def test_long_request_requires_hr(system):
    a,_=system;r=a.submit(a.employee('employee'),payload(span=10));assert r['needs_hr']
    approve(a,r)
    with pytest.raises(ValueError,match='all necessary'):
        a.finish({**r,'action':'approve'},None)
    approve(a,r,True)
    assert a.finish({**r,'action':'approve'},None)['status']=='APPROVED'


def test_wrong_manager_and_self_approval(system):
    a,_=system;r=a.submit(a.employee('employee'),payload());d={**r,'stage':'manager','decision':'approve'}
    with pytest.raises(PermissionError):a.decide(a.employee('other'),d)
    with pytest.raises(PermissionError):a.decide(a.employee('employee'),d)


def test_duplicate_decision_rejected(system):
    a,_=system;r=a.submit(a.employee('employee'),payload());d={**r,'stage':'manager','decision':'approve'}
    a.decide(a.employee('manager'),d)
    with pytest.raises(ClientError):a.decide(a.employee('manager'),d)


def test_signed_link_tamper_expiry(system):
    a,_=system;t=a.sign({'exp':int(time.time())+60,'request_id':'test'})
    assert a.verify(t)['request_id']=='test'
    with pytest.raises(ValueError):a.verify(t[:-1]+('a' if t[-1]!='a' else 'b'))
    with pytest.raises(ValueError):a.verify(a.sign({'exp':0}))


def test_rejection_does_not_debit(system):
    a,_=system;r=a.submit(a.employee('employee'),payload());a.finalize(r,'REJECTED','No cover')
    assert a.balance('employee','casual',r['year'])['used']==0


def test_unlimited_still_tracks_days(system):
    a,_=system;a.table('CONFIG').update_item(Key={'leave_type':'casual'},UpdateExpression='SET unlimited=:u, annual_quota=:q',ExpressionAttributeValues={':u':True,':q':0})
    r=a.submit(a.employee('employee'),payload());assert r['status']=='PENDING_MANAGER'
    a.finalize(approve(a,r),'APPROVED','ok')
    assert a.balance('employee','casual',r['year'])['used']==1


def test_weekly_carry_once(system):
    a,_=system;y=date.today().year;b=a.balance('employee','casual',y-1)
    a.table('BALANCES').update_item(Key={'employee_id':'employee','balance_key':b['balance_key']},UpdateExpression='SET used=:n',ExpressionAttributeValues={':n':9})
    with patch.object(a,'email') as mail:
        a.weekly({},None);a.weekly({},None)
        assert mail.call_count==4
    assert a.balance('employee','casual',y)['carry']==3


def test_inaction_reminder_preserves_balance(system):
    a,_=system;r=a.submit(a.employee('employee'),payload())
    with patch.object(a,'email') as mail,patch.object(a.SNS,'publish') as sns:
        a.remind({**r,'stage':'manager'},None)
        assert mail.call_count==1 and sns.call_count==1
    assert a.request(r['employee_id'],r['request_id'])['status']=='PENDING_MANAGER'
    assert a.balance('employee','casual',r['year'])['used']==0


def test_rule_validation(system):
    rule={**system[1],'annual_quota':-1}
    with pytest.raises(ValueError):validate_rule(rule)


def test_stale_balance_version_rolls_back_all_writes(system):
    a,_=system;r=approve(a,a.submit(a.employee('employee'),payload()))
    original=a.TX.transact_write_items
    def race(**kwargs):
        b=a.balance('employee','casual',r['year'])
        a.table('BALANCES').update_item(Key={'employee_id':'employee','balance_key':b['balance_key']},
            UpdateExpression='SET #v=#v+:one',ExpressionAttributeNames={'#v':'version'},ExpressionAttributeValues={':one':1})
        return original(**kwargs)
    with patch.object(a.TX,'transact_write_items',side_effect=race):
        with pytest.raises(ClientError):a.finalize(r,'APPROVED','ok')
    assert a.request('employee',r['request_id'])['status']=='PENDING_MANAGER'
    assert a.balance('employee','casual',r['year'])['used']==0
    assert a.get('LOCKS',{'employee_id':'employee','leave_date':r['start_date']}) is None
    assert a.finalize(r,'APPROVED','retry')=='APPROVED'


def test_early_decision_replayed_on_task_registration(system):
    a,_=system;r=a.submit(a.employee('employee'),payload())
    a.decide(a.employee('manager'),{**r,'stage':'manager','decision':'approve'})
    with patch.object(a.SF,'send_task_success') as cb,patch.object(a.SNS,'publish') as pub:
        a.register({**r,'stage':'manager','token':'new-callback-token'},None)
        assert cb.call_count==1 and pub.call_count==0
        assert json.loads(cb.call_args.kwargs['output'])=={'decision':'approve'}


def test_public_response_removes_approval_secrets(system):
    a,_=system;r=a.submit(a.employee('employee'),payload())
    r=a.request('employee',r['request_id']);r['manager_token']='private'
    clean=a.public(r)
    assert 'nonce' not in clean and 'manager_token' not in clean


def test_disabled_type_rejected_before_final_debit(system):
    a,_=system;r=approve(a,a.submit(a.employee('employee'),payload()))
    a.table('CONFIG').update_item(Key={'leave_type':'casual'},UpdateExpression='SET enabled=:n',ExpressionAttributeValues={':n':False})
    assert a.finalize(r,'APPROVED','ok')=='REJECTED'
    assert a.balance('employee','casual',r['year'])['used']==0
