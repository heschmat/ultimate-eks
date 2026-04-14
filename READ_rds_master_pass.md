

Turn on RDS-managed master password in Secrets Manager.
The hand-made secret won't be the source of truth for the DB master password anymore.
```sh
aws rds describe-db-instances \
  --db-instance-identifier "$DB_INSTANCE_ID" \
  --region "$AWS_REGION" \
  --query 'DBInstances[0].MasterUserSecret'

# null

# enable RDS-managed master password on the existing instance
aws rds modify-db-instance \
  --db-instance-identifier "$DB_INSTANCE_ID" \
  --manage-master-user-password \
  --apply-immediately \
  --region "$AWS_REGION"

# verify
aws rds describe-db-instances \
  --db-instance-identifier "$DB_INSTANCE_ID" \
  --region "$AWS_REGION" \
  --query 'DBInstances[0].MasterUserSecret'

# {
#     "SecretArn": "arn:aws:secretsmanager:us-east-1:854912240456:secret:rds!db-07a5c9bd-eaf9-46ac-8cba-abd6aebbecb6-bo6CvQ",
#     "SecretStatus": "rotating",
#     "KmsKeyId": "arn:aws:kms:us-east-1:854912240456:key/6100749b-9302-4af9-988a-007505af1384"
# }
```

Update IAM policy so ESO can read the RDS-managed secret.
```sh
# get the new secret ARN
RDS_SECRET_ARN=$(aws rds describe-db-instances \
  --db-instance-identifier "$DB_INSTANCE_ID" \
  --region "$AWS_REGION" \
  --query 'DBInstances[0].MasterUserSecret.SecretArn' \
  --output text)

echo "$RDS_SECRET_ARN"
# arn:aws:secretsmanager:us-east-1:854912240456:secret:rds!db-07a5c9bd-eaf9-46ac-8cba-abd6aebbecb6-bo6CvQ

```

Next grant ESO read access to that secret ARN.
```sh
# ! is special character, hence the single quote around the ID
export SECRET_ID='rds!db-07a5c9bd-eaf9-46ac-8cba-abd6aebbecb6-bo6CvQ'

envsubst < ./secrets/eso-secretsmanager-policy.json.tpl > eso-sm-policy.json

cat eso-sm-policy.json 
# {
#   "Version": "2012-10-17",
#   "Statement": [
#     {
#       "Sid": "ReadFastApiDbSecret",
#       "Effect": "Allow",
#       "Action": [
#         "secretsmanager:GetSecretValue",
#         "secretsmanager:DescribeSecret"
#       ],
#       "Resource": "arn:aws:secretsmanager:us-east-1:854912240456:secret:rds!db-07a5c9bd-eaf9-46ac-8cba-abd6aebbecb6-bo6CvQ"
#     }
#   ]
# }
```

Update the policy:
```sh
# create a new policy version & set it as default
aws iam create-policy-version \
  --policy-arn arn:aws:iam::854912240456:policy/FastApiAppEsoReadSecret \
  --policy-document file://eso-sm-policy.json \
  --set-as-default

```

Inspect the shape of the RDS-managed secret
```sh
aws secretsmanager get-secret-value \
  --secret-id "$RDS_SECRET_ARN" \
  --region "$AWS_REGION"
# {
#     "ARN": "arn:aws:secretsmanager:us-east-1:854912240456:secret:rds!db-07a5c9bd-eaf9-46ac-8cba-abd6aebbecb6-bo6CvQ",
#     "Name": "rds!db-07a5c9bd-eaf9-46ac-8cba-abd6aebbecb6",
#     "VersionId": "4ddb8b3c-e1d2-4d69-a6db-5632e2cc6636",
#     "SecretString": "{\"username\":\"appuser\",\"password\":\"yyw<N7*>~>cr[K?Z0s$*6s!DKrHp\"}",
#     "VersionStages": [
#         "AWSCURRENT",
#         "AWSPENDING"
#     ],
#     "CreatedDate": "2026-04-14T14:25:10.365000+00:00"
# }

```
NOTE: the `SecretString` is of shape `{"username": ..., "password": ...}.
Notice that in the `remoteRef` we've changed the values for both `key` (the value from RDS_SECRET_ARN) & `property` (from `DB_PASSWORD` to `password`).

Update your ExternalSecret to use the RDS-managed secret
```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: app-db-secret
  namespace: default
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: aws-secretsmanager
    kind: SecretStore
  target:
    name: app-db-secret
    creationPolicy: Owner
  data:
    - secretKey: DB_PASSWORD
      remoteRef:
        key: arn:aws:secretsmanager:us-east-1:854912240456:secret:rds!db-xxxx
        property: password

```
Apply it:
```sh
kubectl apply -f ./secrets/externalsecret.yaml

# force a refresh
kubectl annotate es app-db-secret -n default force-sync=$(date +%s) --overwrite
```




As our app uses env vars, we need to restart it so it picks up the new value; Kubernetes env vars from Secrets are read at container start, not hot-reloaded into existing processes.
```sh
kubectl rollout restart deploy/fastapi-eks-demo

```


```sh



aws secretsmanager get-secret-value \
  --region $AWS_REGION \
  --secret-id 'rds!db-35ad18f6-4a2d-48f8-8de0-85f7ec0aa9d1'

```