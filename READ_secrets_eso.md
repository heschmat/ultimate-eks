
```sh

# creates a brand-new secret in AWS Secrets Manager (first time only)
aws secretsmanager create-secret \
  --region $AWS_REGION \
  --name prod/fastapi-app/db \
  --secret-string '{"DB_PASSWORD":"TopSecretPassword4DB<>"}'


aws secretsmanager put-secret-value \
  --secret-id prod/fastapi-app/db \
  --secret-string '{"DB_PASSWORD":"DoesThisSmellFunnyToYou?"}'


aws secretsmanager describe-secret \
  --region $AWS_REGION \
  --secret-id prod/fastapi-app/db


# you can get the actual password value
aws secretsmanager get-secret-value \
  --region $AWS_REGION \
  --secret-id prod/fastapi-app/db
# {
#     "ARN": "arn:aws:secretsmanager:us-east-1:854912240456:secret:prod/fastapi-app/db-D2rE1W",
#     "Name": "prod/fastapi-app/db",
#     "VersionId": "ec069a86-f8a2-4971-817c-1c4e4160267c",
#     "SecretString": "{\"DB_PASSWORD\":\"DoesThisSmellFunnyToYou?\"}",
#     "VersionStages": [
#         "AWSCURRENT"
#     ],
#     "CreatedDate": "2026-04-10T03:41:02.812000+00:00"
# }
```

At this point, the password is no longer something we manually created inside Kubernetes.   
AWS Secrets Manager becomes the source of truth.


```sh
# verify the agent is running (we've already done it for S3 section (IRSA vs. Pod Identity))
# one per node
kubectl get pods -n kube-system | grep eks-pod-identity-agent
# eks-pod-identity-agent-jdqt5      1/1     Running   0          24m
# eks-pod-identity-agent-tfknh      1/1     Running   0          24m
```

🪧 We won't be creating another role & attach the policy. We simply use the already generated role.
But perhaps we had to pick a better naming than `FastApiS3Role`.

Create an IAM policy that can read only this one secret.

```sh
# ⚠️ make sure the `aws region`, `aws account id` & the secret name are correct.
# export AWS_REGION=us-east-1
# export AWS_ACCOUNT_ID=854912240456
# export SECRET_ID="prod/fastapi-app/db*"

# this was our secret 👇
# "arn:aws:secretsmanager:us-east-1:854912240456:secret:prod/fastapi-app/db-D2rE1W"

envsubst < ./secrets/eso-secretsmanager-policy.json.tpl > eso-sm-policy.json

aws iam create-policy \
  --policy-name FastApiAppEsoReadSecret \
  --policy-document file://eso-sm-policy.json

# attach the policy
aws iam attach-role-policy \
  --role-name $IAM_ROLE_NAME \
  --policy-arn arn:aws:iam::$AWS_ACCOUNT_ID:policy/FastApiAppEsoReadSecret

# verify the policies are attached to our role:
aws iam list-attached-role-policies --role-name $IAM_ROLE_NAME
# {
#     "AttachedPolicies": [
#         {
#             "PolicyName": "FastApiAppEsoReadSecret",
#             "PolicyArn": "arn:aws:iam::854912240456:policy/FastApiAppEsoReadSecret"
#         },
#         {
#             "PolicyName": "FastApiS3Policy",
#             "PolicyArn": "arn:aws:iam::854912240456:policy/FastApiS3Policy"
#         }
#     ]
# }
```

### Install External Secrets Operator (ESO)

```sh
helm repo add external-secrets https://charts.external-secrets.io
helm repo update

helm install external-secrets external-secrets/external-secrets \
  -n external-secrets \
  --create-namespace \
  --set installCRDs=true

# verify the controller is running:
kubectl get pods -n external-secrets -w

kubectl get sa -n external-secrets
# NAME                               SECRETS   AGE
# default                            0         63s
# external-secrets                   0         63s ✅
# external-secrets-cert-controller   0         63s
# external-secrets-webhook           0         63s
```

Associate the IAM role to the ESO service account with Pod Identity.
```sh
aws eks create-pod-identity-association \
  --cluster-name $CLUSTER_NAME \
  --namespace external-secrets \
  --service-account external-secrets \
  --role-arn arn:aws:iam::$AWS_ACCOUNT_ID:role/$IAM_ROLE_NAME


# verify it exists
aws eks list-pod-identity-associations --cluster-name $CLUSTER_NAME --region $AWS_REGION
# {
#     "associations": [
#         {
#             "clusterName": "fastapi-eks-demo",
#             "namespace": "external-secrets",
#             "serviceAccount": "external-secrets",
#             "associationArn": "arn:aws:eks:us-east-1:854912240456:podidentityassociation/fastapi-eks-demo/a-vmpqzwzvprx9gbnms",
#             "associationId": "a-vmpqzwzvprx9gbnms"
#         },
#         {
#             "clusterName": "fastapi-eks-demo",
#             "namespace": "default",
#             "serviceAccount": "pod-ident-sa",
#             "associationArn": "arn:aws:eks:us-east-1:854912240456:podidentityassociation/fastapi-eks-demo/a-z5jsgfv08qfy3nzgt",
#             "associationId": "a-z5jsgfv08qfy3nzgt"
#         }
#     ]
# }

# pay attention to this section: 👇
# {
#   "associations": [
#     {
#       "namespace": "external-secrets",
#       "serviceAccount": "external-secrets"
#     },
#     {
#       "namespace": "default",
#       "serviceAccount": "pod-ident-sa"
#     }
#   ]
# }

# verify the mapping between the service account & the role:
aws eks describe-pod-identity-association \
  --cluster-name $CLUSTER_NAME \
  --association-id a-vmpqzwzvprx9gbnms \
  --region $AWS_REGION

# {
#     "association": {
#         "clusterName": "fastapi-eks-demo",
#         "namespace": "external-secrets",
#         "serviceAccount": "external-secrets",
#         "roleArn": "arn:aws:iam::854912240456:role/FastApiS3Role",
#         "associationArn": "arn:aws:eks:us-east-1:854912240456:podidentityassociation/fastapi-eks-demo/a-vmpqzwzvprx9gbnms",
#         "associationId": "a-vmpqzwzvprx9gbnms",
#         ...
#     }
# }
```


Create SecretStore
```sh
kubectl apply -f ./secrets/secretstore.yaml


kubectl get secretstore -n default
# NAME                 AGE   STATUS   CAPABILITIES   READY
# aws-secretsmanager   68s   Valid    ReadWrite      True

kubectl describe secretstore aws-secretsmanager -n default
```


Create the `ExternalSecret`
This is the object that says, "Read `DB_PASSWORD` from `AWS Secrets Manager` and write it into a Kubernetes Secret called `app-db-secret`."
⚠️ Make sure it's with how the pod expects the secret
e.g., secret name (app-db-secret), secret key (DB_PASSWORD), secret_id from aws secrets manager (prod/fastapi-app/db)
```sh
kubectl apply -f ./secrets/externalsecret.yaml


# verify ESO created the native Kubernetes Secret:
kubectl get externalsecret -n default
# NAME            STORETYPE     STORE                REFRESH INTERVAL   STATUS         READY   LAST SYNC
# app-db-secret   SecretStore   aws-secretsmanager   1h                 SecretSynced   True    9m1s


# 📢
kubectl describe es app-db-secret -n default
# Events:
#   Type     Reason        Age    From              Message
#   ----     ------        ----   ----              -------
#   Warning  UpdateFailed  2m30s  external-secrets  error processing spec.data[0] (key: prod/fastapi-app/db), err: operation error Secrets Manager: GetSecretValue, https response error StatusCode: 400, RequestID: 6c1b9f39-d8c5-42b2-9f8c-20fd762636bf, api error AccessDeniedException: User: arn:aws:sts::854912240456:assumed-role/eksctl-fastapi-eks-demo-nodegroup--NodeInstanceRole-wf5kOuwavDhr/i-038912cf83879ef28 is not authorized to perform: secretsmanager:GetSecretValue on resource: prod/fastapi-app/db because no identity-based policy allows the secretsmanager:GetSecretValue action

# we need to restart 
kubectl rollout restart deploy/external-secrets -n external-secrets
kubectl rollout status deploy/external-secrets -n external-secrets

# now we should see the expected result.
kubectl describe es app-db-secret -n default
# ...
# Normal   Created       9m23s  external-secrets  secret created


kubectl get secret app-db-secret -n default
# NAME            TYPE     DATA   AGE
# app-db-secret   Opaque   1      10m

```

If the Secret appears, the AWS → ESO → Kubernetes chain is working. You can confirm the created Kubernetes Secret with:
```sh
kubectl get secret app-db-secret -n default -o jsonpath='{.data.DB_PASSWORD}' | base64 -d && echo
# DoesThisSmellFunnyToYou?
```

## password update
If we update the password in AWS SecretsManager, it will be picked up after the interval period is passed by the ESO and then `app-db-secret` is also updated.
```sh
aws secretsmanager put-secret-value \
  --secret-id prod/fastapi-app/db \
  --secret-string '{"DB_PASSWORD":"DoesThisSmellFunnyToYou?"}'

kubectl get secret app-db-secret -n default \
  -o jsonpath='{.data.DB_PASSWORD}' | base64 -d && echo

# wait for 1m (refresh interval specified in `externalsecret`) for update:
kubectl get secret app-db-secret -n default \
  -o jsonpath='{.data.DB_PASSWORD}' | base64 -d && echo

# or you can force immediate refresh right now
kubectl annotate externalsecret app-db-secret \
  -n default force-sync=$(date +%s) --overwrite

```

⚠️ The way our app & db is setup, we face issues: neither the pod, nor the RDS will notice the password update. 

In fact, if we restart the pod it won't work as the new password (new pod picks it up from the updated `app-db-secret`) does NOT match that of RDS.

```sh
k rollout restart deploy/fastapi-eks-demo
# deployment.apps/fastapi-eks-demo restarted

# the new pod won't become READY
kgp -w
# NAME                                READY   STATUS    RESTARTS   AGE
# fastapi-eks-demo-7cb8677b6b-5zp82   1/1     Running   0          19s
# fastapi-eks-demo-857698f6d4-7cxqj   0/1     Pending   0          0s
# fastapi-eks-demo-857698f6d4-7cxqj   0/1     Pending   0          0s
# fastapi-eks-demo-857698f6d4-7cxqj   0/1     ContainerCreating   0          0s
# fastapi-eks-demo-857698f6d4-7cxqj   0/1     Running            0          1s

k describe pod fastapi-eks-demo-857698f6d4-7cxqj
# Events:
#   Type     Reason     Age                From               Message
#   ----     ------     ----               ----               -------
#   Normal   Scheduled  55s                default-scheduler  Successfully assigned default/fastapi-eks-demo-857698f6d4-7cxqj to ip-192-168-4-214.ec2.internal
#   Normal   Pulling    55s                kubelet            spec.containers{app}: Pulling image "854912240456.dkr.ecr.us-east-1.amazonaws.com/fastapi-s3-pg:latest"
#   Normal   Pulled     54s                kubelet            spec.containers{app}: Successfully pulled image "854912240456.dkr.ecr.us-east-1.amazonaws.com/fastapi-s3-pg:latest" in 161ms (161ms including waiting). Image size: 77283495 bytes.
#   Normal   Created    54s                kubelet            spec.containers{app}: Created container: app
#   Normal   Started    54s                kubelet            spec.containers{app}: Started container app
#   Warning  Unhealthy  0s (x11 over 50s)  kubelet            spec.containers{app}: Startup probe failed: Get "http://192.168.17.100:8000/healthz": dial tcp 192.168.17.100:8000: connect: connection refused ❌❌

k logs fastapi-eks-demo-857698f6d4-7cxqj --tail 10
#   File "/usr/local/lib/python3.12/site-packages/psycopg/connection.py", line 122, in connect
#     raise last_ex.with_traceback(None)
# psycopg.OperationalError: connection failed: connection to server at "192.168.79.177", port 5432 failed: FATAL:  password authentication failed for user "appuser"
# connection to server at "192.168.79.177", port 5432 failed: FATAL:  no pg_hba.conf entry for host "192.168.17.100", user "appuser", database "appdb", no encryption

# ERROR:    Application startup failed. Exiting. ❌❌
# INFO:     Waiting for child process [146]
# INFO:     Child process [146] died

```
