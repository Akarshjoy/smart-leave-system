import argparse
import json
from pathlib import Path
import boto3

p=argparse.ArgumentParser(description='Generate a reviewable deployment policy; does not attach it.')
p.add_argument('--profile',default='my-account')
p.add_argument('--region',default='us-east-1')
p.add_argument('--stack',default='smart-leave')
a=p.parse_args()
s=boto3.Session(profile_name=a.profile,region_name=a.region)
account=s.client('sts').get_caller_identity()['Account']
region=a.region
stack=a.stack
statements=[]
def allow(actions,resources,condition=None):
    item={'Effect':'Allow','Action':actions,'Resource':resources}
    if condition:item['Condition']=condition
    statements.append(item)
allow('cloudformation:*',[
    f'arn:aws:cloudformation:{region}:{account}:stack/{stack}/*',
    f'arn:aws:cloudformation:{region}:{account}:stack/{stack}-*/*',
    f'arn:aws:cloudformation:{region}:{account}:stack/aws-sam-cli-managed-default/*',
    f'arn:aws:cloudformation:{region}:{account}:changeSet/*/*',
    f'arn:aws:cloudformation:{region}:aws:transform/Serverless-2016-10-31'])
allow(['cloudformation:ValidateTemplate','cloudformation:GetTemplateSummary'],'*')
allow('s3:*',[f'arn:aws:s3:::{stack}-*',f'arn:aws:s3:::{stack}-*/*','arn:aws:s3:::aws-sam-cli-managed-default-*','arn:aws:s3:::aws-sam-cli-managed-default-*/*'])
for service,resource in [('lambda','function'),('dynamodb','table'),('states','stateMachine'),('states','execution'),('logs','log-group'),('events','rule'),('secretsmanager','secret')]:
    prefix='/aws/lambda/' if service=='logs' else ''
    sep=':' if service in ('lambda','states','logs','secretsmanager') else '/'
    allow(service+':*',f'arn:aws:{service}:{region}:{account}:{resource}{sep}{prefix}{stack}-*')
allow(['sns:*'],f'arn:aws:sns:{region}:{account}:{stack}-*')
allow(['sqs:*'],f'arn:aws:sqs:{region}:{account}:{stack}-*')
allow(['cloudwatch:PutMetricAlarm','cloudwatch:DescribeAlarms','cloudwatch:DeleteAlarms','cloudwatch:TagResource','cloudwatch:UntagResource','cloudwatch:ListTagsForResource'],f'arn:aws:cloudwatch:{region}:{account}:alarm:{stack}-*')
allow(['iam:CreateRole','iam:GetRole','iam:DeleteRole','iam:UpdateAssumeRolePolicy','iam:PutRolePolicy','iam:GetRolePolicy','iam:DeleteRolePolicy','iam:AttachRolePolicy','iam:DetachRolePolicy','iam:ListRolePolicies','iam:ListAttachedRolePolicies','iam:TagRole','iam:UntagRole'],f'arn:aws:iam::{account}:role/{stack}-*')
allow('iam:PassRole',f'arn:aws:iam::{account}:role/{stack}-*',{'StringEquals':{'iam:PassedToService':['lambda.amazonaws.com','states.amazonaws.com']}})
allow('iam:CreateServiceLinkedRole',f'arn:aws:iam::{account}:role/aws-service-role/email.cognito-idp.amazonaws.com/*',{'StringEquals':{'iam:AWSServiceName':'email.cognito-idp.amazonaws.com'}})
allow('cognito-idp:*',f'arn:aws:cognito-idp:{region}:{account}:userpool/*')
allow('cognito-idp:CreateUserPool','*')
allow('secretsmanager:GetRandomPassword','*')
allow('apigateway:*',f'arn:aws:apigateway:{region}::*')
allow(['cloudfront:CreateDistribution','cloudfront:GetDistribution','cloudfront:GetDistributionConfig','cloudfront:UpdateDistribution','cloudfront:DeleteDistribution','cloudfront:TagResource','cloudfront:UntagResource','cloudfront:ListTagsForResource','cloudfront:CreateOriginAccessControl','cloudfront:GetOriginAccessControl','cloudfront:UpdateOriginAccessControl','cloudfront:DeleteOriginAccessControl'],'*')
policy={'Version':'2012-10-17','Statement':statements}
assert len(json.dumps(policy,separators=(',',':')))<=6144,'Policy exceeds customer-managed policy size limit.'
path=Path(__file__).resolve().parents[1]/'deployment-policy.json'
path.write_text(json.dumps(policy,indent=2)+'\n')
print('Created deployment-policy.json. Ask an account administrator to review it and attach it as a customer-managed policy.')
