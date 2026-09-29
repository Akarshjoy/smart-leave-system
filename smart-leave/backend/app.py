import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError
from domain import days_between, remaining, validate_rule

DB = boto3.resource('dynamodb')
TX = boto3.client('dynamodb')
SF = boto3.client('stepfunctions')
SES = boto3.client('ses')
SNS = boto3.client('sns')
SER = TypeSerializer()
DES = TypeDeserializer()
TERMINAL = {'APPROVED', 'REJECTED', 'EXPIRED'}


def table(name):
    return DB.Table(os.environ[name])


def now():
    return datetime.now(timezone.utc).isoformat()


def enc(data):
    return {k: SER.serialize(v) for k, v in data.items()}


def get(name, key):
    return table(name).get_item(Key=key, ConsistentRead=True).get('Item')


def scan(name):
    args = {'ConsistentRead': True}
    items = []
    while True:
        page = table(name).scan(**args)
        items.extend(page['Items'])
        if 'LastEvaluatedKey' not in page:
            return items
        args['ExclusiveStartKey'] = page['LastEvaluatedKey']


def employee(eid):
    item = get('PEOPLE', {'employee_id': eid})
    if not item or not item.get('active'):
        raise ValueError('Employee profile not found or inactive.')
    return item


def request(eid, rid):
    item = get('REQUESTS', {'employee_id': eid, 'request_id': rid})
    if not item:
        raise ValueError('Request not found.')
    return item


def history(eid):
    args = {'KeyConditionExpression': Key('employee_id').eq(eid), 'ConsistentRead': True}
    items = []
    while True:
        page = table('REQUESTS').query(**args)
        items.extend(page['Items'])
        if 'LastEvaluatedKey' not in page:
            return items
        args['ExclusiveStartKey'] = page['LastEvaluatedKey']


def balance(eid, kind, year):
    key = {'employee_id': eid, 'balance_key': f'{year}#{kind}'}
    item = get('BALANCES', key)
    if item:
        return item
    item = {**key, 'used': 0, 'carry': 0, 'version': 0, 'carry_applied': False, 'quota_basis': int((get('CONFIG', {'leave_type': kind}) or {}).get('annual_quota', 0))}
    try:
        table('BALANCES').put_item(Item=item, ConditionExpression='attribute_not_exists(employee_id)')
    except ClientError as exc:
        if exc.response['Error']['Code'] != 'ConditionalCheckFailedException':
            raise
    return get('BALANCES', key)


def put_tx(name, item, condition=None):
    data = {'TableName': os.environ[name], 'Item': enc(item)}
    if condition:
        data['ConditionExpression'] = condition
    return {'Put': data}


def email(address, subject, body):
    SES.send_email(Source=os.environ['SENDER'], Destination={'ToAddresses': [address]},
                   Message={'Subject': {'Data': subject}, 'Body': {'Text': {'Data': body}}})


def public(item):
    return {k: v for k, v in item.items() if not k.endswith('_token') and k != 'nonce'}


def sign(payload):
    secret = boto3.client('secretsmanager').get_secret_value(SecretId=os.environ['SIGNING_SECRET'])['SecretString'].encode()
    part = base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode()).decode().rstrip('=')
    sig = hmac.new(secret, part.encode(), hashlib.sha256).hexdigest()
    return part + '.' + sig


def verify(token):
    if len(token) > 3000:
        raise ValueError('Invalid approval link.')
    try:
        part, signature = token.split('.')
        data = json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
        expected = sign(data).split('.')[1]
        if not hmac.compare_digest(signature, expected) or int(data['exp']) <= int(time.time()):
            raise ValueError('Approval link is invalid or expired. Open the approvals dashboard.')
        return data
    except (KeyError, TypeError, json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError('Invalid approval link.') from exc


def submit(person, data):
    rid = str(uuid.UUID(data['request_id']))
    old = get('REQUESTS', {'employee_id': person['employee_id'], 'request_id': rid})
    if old:
        return public(old)
    manager = employee(person.get('manager_id', ''))
    if manager['role'] != 'Manager' or manager['employee_id'] == person['employee_id']:
        raise ValueError('An independent manager must be assigned before applying.')
    rule = get('CONFIG', {'leave_type': data['leave_type']})
    if not rule or not rule['enabled']:
        raise ValueError('This leave type is unavailable.')
    dates, count = days_between(data['start_date'], data['end_date'], rule)
    reason = str(data.get('reason', '')).strip()
    if not 1 <= len(reason) <= 500:
        raise ValueError('Give a reason between 1 and 500 characters.')
    year = int(data['start_date'][:4])
    b = balance(person['employee_id'], rule['leave_type'], year)
    rejection = ''
    if not rule['unlimited'] and remaining(rule, b) < count:
        rejection = 'Insufficient available leave balance.'
    if any(x['status'] == 'APPROVED' and x['start_date'] <= data['end_date'] and x['end_date'] >= data['start_date'] for x in history(person['employee_id'])):
        rejection = 'Dates overlap an approved leave request.'
    item = {'employee_id': person['employee_id'], 'request_id': rid, 'name': person['name'],
            'email': person['email'], 'manager_id': manager['employee_id'], 'leave_type': rule['leave_type'],
            'start_date': data['start_date'], 'end_date': data['end_date'], 'dates': dates, 'days': count,
            'year': year, 'needs_hr': count > 5, 'reason': reason, 'created_at': now(),
            'status': 'REJECTED' if rejection else 'PENDING_MANAGER', 'outcome_reason': rejection,
            'rule_snapshot': rule, 'nonce': str(uuid.uuid4())}
    try:
        table('REQUESTS').put_item(Item=item, ConditionExpression='attribute_not_exists(request_id)')
    except ClientError as exc:
        if exc.response['Error']['Code'] != 'ConditionalCheckFailedException':
            raise
        item = request(person['employee_id'], rid)
    return public(item)


def authorized(person, item, stage):
    if person['employee_id'] == item['employee_id']:
        return False
    if stage == 'manager':
        return person['role'] == 'Manager' and person['employee_id'] == item['manager_id']
    return stage == 'hr' and person['role'] == 'HRAdmin'


def callback(item, stage):
    token = item.get(stage + '_token')
    decision = item.get(stage + '_decision')
    if token and decision:
        try:
            SF.send_task_success(taskToken=token, output=json.dumps({'decision': decision}))
        except ClientError as exc:
            if exc.response['Error']['Code'] not in ('TaskTimedOut', 'TaskDoesNotExist', 'InvalidToken'):
                raise


def decide(person, data):
    item = request(data['employee_id'], data['request_id'])
    stage = data['stage']
    if not authorized(person, item, stage):
        raise PermissionError('You cannot approve this request.')
    if data.get('token'):
        link = verify(data['token'])
        for key in ('employee_id', 'request_id', 'nonce'):
            if link.get(key) != item[key]:
                raise ValueError('Approval link does not match this request.')
        if link.get('stage') != stage:
            raise ValueError('Approval stage does not match.')
    decision = data['decision']
    note = str(data.get('note', '')).strip()
    if decision not in ('approve', 'reject') or len(note) > 500 or (decision == 'reject' and not note):
        raise ValueError('Choose approve/reject; rejection needs a reason (maximum 500 characters).')
    expected = 'PENDING_' + stage.upper()
    table('REQUESTS').update_item(Key={'employee_id': item['employee_id'], 'request_id': item['request_id']},
        UpdateExpression='SET #decision=:d, #actor=:a, #at=:t, #note=:n',
        ConditionExpression='#s=:s AND attribute_not_exists(#decision)',
        ExpressionAttributeNames={'#decision': stage+'_decision', '#actor': stage+'_actor', '#at': stage+'_at', '#note': stage+'_note', '#s': 'status'},
        ExpressionAttributeValues={':d': decision, ':a': person['employee_id'], ':t': now(), ':n': note, ':s': expected})
    return {'message': 'Decision recorded. The workflow will update the final status shortly.'}


def api(event, context):
    try:
        path = event.get('path', '')
        method = event.get('httpMethod', 'GET')
        if path == '/approval' and method == 'GET':
            token = (event.get('queryStringParameters') or {}).get('token', '')
            verify(token)
            return {'statusCode': 302, 'headers': {'Location': os.environ['PORTAL'] + '/#approval=' + token,
                    'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'}, 'body': ''}
        claims = event['requestContext']['authorizer']['claims']
        person = employee(claims.get('cognito:username', claims['sub']))
        data = json.loads(event.get('body') or '{}')
        if path == '/me' and method == 'GET':
            rules = scan('CONFIG')
            years = [date.today().year, date.today().year + 1]
            balances = []
            for y in years:
                for rule in rules:
                    b = balance(person['employee_id'], rule['leave_type'], y)
                    balances.append({**b, 'leave_type': rule['leave_type'], 'year': y,
                                     'remaining': None if rule['unlimited'] else remaining(rule, b)})
            result = {'person': person, 'rules': rules, 'balances': balances, 'requests': [public(x) for x in history(person['employee_id'])]}
        elif path == '/requests' and method == 'POST':
            result = submit(person, data)
        elif path == '/team' and method == 'GET':
            if person['role'] not in ('Manager', 'HRAdmin'):
                raise PermissionError('Manager or HR access required.')
            result = [public(x) for x in scan('REQUESTS') if person['role'] == 'HRAdmin' or x['manager_id'] == person['employee_id']]
        elif path == '/decisions' and method == 'POST':
            result = decide(person, data)
        elif path == '/config' and method == 'POST':
            if person['role'] != 'HRAdmin':
                raise PermissionError('HR access required.')
            rule = validate_rule(data)
            old = get('CONFIG', {'leave_type': rule['leave_type']})
            expected = str(data.get('version', ''))
            rule['version'] = str(uuid.uuid4())
            rule['updated_at'] = now()
            rule['updated_by'] = person['employee_id']
            args = {'Item': rule, 'ConditionExpression': 'attribute_not_exists(leave_type)'}
            if old:
                args.update(ConditionExpression='#v=:v', ExpressionAttributeNames={'#v': 'version'}, ExpressionAttributeValues={':v': expected})
            table('CONFIG').put_item(**args)
            result = rule
        else:
            return response(404, {'error': 'Route not found.'})
        return response(200, result)
    except PermissionError as exc:
        return response(403, {'error': str(exc)})
    except (ValueError, KeyError, TypeError) as exc:
        return response(400, {'error': str(exc)})
    except ClientError as exc:
        code = exc.response['Error']['Code']
        if code in ('ConditionalCheckFailedException', 'TransactionCanceledException'):
            return response(409, {'error': 'This record changed or the decision was already submitted. Refresh and review.'})
        print('AWS failure', code)
        return response(500, {'error': 'AWS operation failed. Check the API Lambda logs.'})


def response(status, data):
    return {'statusCode': status, 'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': os.environ['PORTAL'],
            'Cache-Control': 'no-store'}, 'body': json.dumps(data, default=lambda v: int(v) if isinstance(v, Decimal) else str(v))}


def register(event, context):
    item = request(event['employee_id'], event['request_id'])
    stage = event['stage']
    status = 'PENDING_' + stage.upper()
    if item['status'] in TERMINAL:
        SF.send_task_success(taskToken=event['token'], output=json.dumps({'decision': 'reject'}))
        return
    table('REQUESTS').update_item(Key={'employee_id': item['employee_id'], 'request_id': item['request_id']},
        UpdateExpression='SET #t=:t, #s=:s', ConditionExpression='#s=:before',
        ExpressionAttributeNames={'#t': stage+'_token', '#s': 'status'},
        ExpressionAttributeValues={':t': event['token'], ':s': status, ':before': item['status']})
    item = request(item['employee_id'], item['request_id'])
    if item.get(stage+'_decision'):
        callback(item, stage)
        return
    ttl = int(os.environ.get('APPROVAL_SECONDS', '172800'))
    token = sign({'employee_id': item['employee_id'], 'request_id': item['request_id'], 'stage': stage,
                  'nonce': item['nonce'], 'exp': int(time.time()) + ttl})
    url = os.environ['API_BASE'] + '/approval?token=' + token
    body = f"Leave request {item['request_id']} for {item['name']}: {item['start_date']} to {item['end_date']} ({item['days']} working/chargeable days).\nReview and sign in to approve/reject: {url}\nOpening this link does not make a decision."
    if stage == 'manager':
        SNS.publish(TopicArn=os.environ['MANAGER_TOPIC'], Subject='Leave request requires manager review', Message=body,
                    MessageAttributes={'manager_id': {'DataType': 'String', 'StringValue': item['manager_id']}})
    else:
        email(os.environ['HR_EMAIL'], 'Leave request requires HR approval', body)


def finalize(item, status, reason):
    if item['status'] in TERMINAL:
        return item['status']
    key = {'employee_id': item['employee_id'], 'request_id': item['request_id']}
    update = {'TableName': os.environ['REQUESTS'], 'Key': enc(key),
              'UpdateExpression': 'SET #s=:new, outcome_reason=:reason, finalized_at=:at',
              'ConditionExpression': '#s=:old', 'ExpressionAttributeNames': {'#s': 'status'},
              'ExpressionAttributeValues': enc({':new': status, ':reason': reason, ':at': now(), ':old': item['status']})}
    ops = [{'Update': update}]
    if status == 'APPROVED':
        rule = get('CONFIG', {'leave_type': item['leave_type']})
        b = balance(item['employee_id'], item['leave_type'], int(item['year']))
        if not rule or not rule['enabled'] or item['start_date'] < date.today().isoformat():
            return finalize(item, 'REJECTED', 'Leave type disabled or start date has passed before final approval.')
        if not rule['unlimited'] and remaining(rule, b) < int(item['days']):
            return finalize(item, 'REJECTED', 'Insufficient balance at final approval.')
        ops.append({'ConditionCheck': {'TableName': os.environ['CONFIG'], 'Key': enc({'leave_type': item['leave_type']}),
                     'ConditionExpression': '#v=:v', 'ExpressionAttributeNames': {'#v': 'version'}, 'ExpressionAttributeValues': enc({':v': rule['version']})}})
        ops.append({'Update': {'TableName': os.environ['BALANCES'], 'Key': enc({'employee_id': item['employee_id'], 'balance_key': b['balance_key']}),
                     'UpdateExpression': 'SET used=used+:n, #v=#v+:one, quota_basis=:quota', 'ConditionExpression': '#v=:v',
                     'ExpressionAttributeNames': {'#v': 'version'}, 'ExpressionAttributeValues': enc({':n': item['days'], ':one': 1, ':v': b['version'], ':quota': int(rule['annual_quota'])})}})
        for day in item['dates']:
            ops.append(put_tx('LOCKS', {'employee_id': item['employee_id'], 'leave_date': day, 'request_id': item['request_id']}, 'attribute_not_exists(employee_id)'))
    try:
        TX.transact_write_items(TransactItems=ops)
    except ClientError as exc:
        if exc.response['Error']['Code'] != 'TransactionCanceledException':
            raise
        fresh = request(item['employee_id'], item['request_id'])
        if fresh['status'] in TERMINAL:
            return fresh['status']
        if status == 'APPROVED' and any(get('LOCKS', {'employee_id': item['employee_id'], 'leave_date': d}) for d in item['dates']):
            return finalize(fresh, 'REJECTED', 'Dates overlap another approved leave request.')
        raise
    return status


def finish(event, context):
    item = request(event['employee_id'], event['request_id'])
    action = event['action']
    if action == 'approve':
        if item.get('manager_decision') != 'approve' or (item['needs_hr'] and item.get('hr_decision') != 'approve'):
            raise ValueError('Final approval requires all necessary decisions.')
        return {'status': finalize(item, 'APPROVED', 'All required approvals received.')}
    if action == 'reject':
        reason = item.get('hr_note') or item.get('manager_note') or 'Rejected by approver.'
        return {'status': finalize(item, 'REJECTED', reason)}
    if action == 'expire':
        return {'status': finalize(item, 'EXPIRED', 'No final decision within 30 days.')}
    raise ValueError('Unknown action.')


def remind(event, context):
    item = request(event['employee_id'], event['request_id'])
    stage = event['stage']
    if item['status'] in TERMINAL or item.get(stage+'_decision'):
        return
    email(os.environ['HR_EMAIL'], 'Leave approval overdue', f"Request {item['request_id']} is waiting for {stage} approval. Please follow up. {os.environ['PORTAL']}")
    if stage == 'manager':
        SNS.publish(TopicArn=os.environ['MANAGER_TOPIC'], Subject='Reminder: leave approval overdue',
                    Message=f"Review request {item['request_id']}: {os.environ['PORTAL']}",
                    MessageAttributes={'manager_id': {'DataType': 'String', 'StringValue': item['manager_id']}})


def changes(event, context):
    for record in event['Records']:
        if record['eventName'] == 'REMOVE':
            continue
        item = {k: DES.deserialize(v) for k, v in record['dynamodb']['NewImage'].items()}
        old = {k: DES.deserialize(v) for k, v in record['dynamodb'].get('OldImage', {}).items()}
        if record['eventName'] == 'INSERT' and item['status'] == 'PENDING_MANAGER':
            args = {'employee_id': item['employee_id'], 'request_id': item['request_id'], 'needs_hr': item['needs_hr']}
            try:
                SF.start_execution(stateMachineArn=os.environ['WORKFLOW'], name=item['request_id'], input=json.dumps(args))
            except ClientError as exc:
                if exc.response['Error']['Code'] != 'ExecutionAlreadyExists':
                    raise
        for stage in ('manager', 'hr'):
            if item.get(stage+'_decision') and item.get(stage+'_decision') != old.get(stage+'_decision'):
                callback(item, stage)
        if old.get('status') != item['status']:
            email(item['email'], 'Leave request: ' + item['status'],
                  f"Request {item['request_id']}\nDates: {item['start_date']} to {item['end_date']}\nStatus: {item['status']}\n{item.get('outcome_reason', '')}\nView: {os.environ['PORTAL']}")


def weekly(event, context):
    year = date.today().year
    rules = scan('CONFIG')
    week = date.today().strftime('%G-W%V')
    for person in scan('PEOPLE'):
        if not person.get('active'):
            continue
        lines = []
        for rule in rules:
            kind = rule['leave_type']
            b = balance(person['employee_id'], kind, year)
            if not b['carry_applied']:
                prev = get('BALANCES', {'employee_id': person['employee_id'], 'balance_key': f'{year-1}#{kind}'})
                amount = min(int(rule['carry_cap']), max(0, int(prev['quota_basis']) + int(prev['carry']) - int(prev['used']))) if prev and not rule['unlimited'] else 0
                try:
                    table('BALANCES').update_item(Key={'employee_id': person['employee_id'], 'balance_key': b['balance_key']},
                        UpdateExpression='SET carry=:c, carry_applied=:yes, #v=#v+:one',
                        ConditionExpression='carry_applied=:no', ExpressionAttributeNames={'#v': 'version'},
                        ExpressionAttributeValues={':c': amount, ':yes': True, ':no': False, ':one': 1})
                except ClientError as exc:
                    if exc.response['Error']['Code'] != 'ConditionalCheckFailedException':
                        raise
                b = get('BALANCES', {'employee_id': person['employee_id'], 'balance_key': b['balance_key']})
            lines.append(f"{kind}: {'unlimited' if rule['unlimited'] else remaining(rule, b)} remaining; {b['used']} used; {b['carry']} carried")
        if person.get('summary_week') != week:
            email(person['email'], 'Weekly leave balance summary', '\n'.join(lines))
            table('PEOPLE').update_item(Key={'employee_id': person['employee_id']}, UpdateExpression='SET summary_week=:w', ExpressionAttributeValues={':w': week})
