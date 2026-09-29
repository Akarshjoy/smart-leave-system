import argparse
import json
import uuid
from pathlib import Path
import boto3
from botocore.exceptions import ClientError

p = argparse.ArgumentParser()
p.add_argument('--profile', default='my-account')
p.add_argument('--region', default='us-east-1')
p.add_argument('--stack', default='smart-leave')
sub = p.add_subparsers(dest='action', required=True)
sub.add_parser('configure')
sub.add_parser('seed')
u = sub.add_parser('user')
u.add_argument('--name', required=True)
u.add_argument('--email', required=True)
u.add_argument('--role', choices=['Employee','Manager','HRAdmin'], required=True)
u.add_argument('--manager-id', default='')
a = p.parse_args()
s = boto3.Session(profile_name=a.profile, region_name=a.region)
o = {x['OutputKey']:x['OutputValue'] for x in s.client('cloudformation').describe_stacks(StackName=a.stack)['Stacks'][0]['Outputs']}
db = s.resource('dynamodb')
if a.action == 'configure':
    config = {'apiUrl':o['ApiUrl'],'clientId':o['ClientId'],'region':o['Region']}
    (Path(__file__).resolve().parents[1]/'frontend/config.js').write_text('window.CONFIG = '+json.dumps(config)+';\n')
    print('Frontend configured. Upload frontend/ to:',o['WebBucket'])
    print('Portal:',o['PortalUrl'])
elif a.action == 'seed':
    for kind, quota, carry, unlimited in [('sick',12,0,False),('casual',12,0,False),('earned',24,10,False),('unpaid',0,0,True)]:
        rule = {'leave_type':kind,'annual_quota':quota,'carry_cap':carry,'unlimited':unlimited,'working_days_only':True,
                'max_calendar_days':60,'enabled':True,'holidays':[],'version':str(uuid.uuid4())}
        try:
            db.Table(o['ConfigTable']).put_item(Item=rule,ConditionExpression='attribute_not_exists(leave_type)')
            print('Created editable demonstration rule:',kind)
        except ClientError as e:
            if e.response['Error']['Code']!='ConditionalCheckFailedException': raise
            print('Existing rule preserved:',kind)
else:
    eid = str(uuid.uuid5(uuid.NAMESPACE_URL, a.stack+':'+a.email.strip().lower()))
    if a.role == 'Employee' and not a.manager_id:
        p.error('Employees require --manager-id (the UUID printed when creating the manager).')
    if a.manager_id:
        manager=db.Table(o['PeopleTable']).get_item(Key={'employee_id':a.manager_id},ConsistentRead=True).get('Item')
        if not manager or manager['role']!='Manager' or not manager['active'] or a.manager_id==eid:
            p.error('Manager must be an existing active Manager with a different identity.')
    person={'employee_id':eid,'name':a.name,'email':a.email.strip().lower(),'role':a.role,'manager_id':a.manager_id,'active':True}
    pool=s.client('cognito-idp')
    try:
        pool.admin_create_user(UserPoolId=o['UserPoolId'],Username=eid,UserAttributes=[{'Name':'email','Value':person['email']},{'Name':'email_verified','Value':'true'}],DesiredDeliveryMediums=['EMAIL'])
    except ClientError as e:
        if e.response['Error']['Code']!='UsernameExistsException': raise
    existing=db.Table(o['PeopleTable']).get_item(Key={'employee_id':eid},ConsistentRead=True).get('Item')
    if existing and any(existing[k]!=person[k] for k in ('name','email','role','manager_id')):
        raise SystemExit('Profile exists with different details. This bootstrap command does not overwrite profiles.')
    pool.admin_add_user_to_group(UserPoolId=o['UserPoolId'],Username=eid,GroupName=a.role)
    if not existing:
        db.Table(o['PeopleTable']).put_item(Item=person,ConditionExpression='attribute_not_exists(employee_id)')
    if a.role=='Manager':
        s.client('sns').subscribe(TopicArn=o['ManagerTopicArn'],Protocol='email',Endpoint=person['email'],
            Attributes={'FilterPolicy':json.dumps({'manager_id':[eid]})},ReturnSubscriptionArn=True)
        print('Confirm the SNS subscription email before requesting leave. Filter activation can take time.')
    print('Employee ID:',eid)
    print('Login email:',person['email'])
