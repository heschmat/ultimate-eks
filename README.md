
# 🚀 FastAPI on AWS EKS with RDS & S3 (End-to-End Setup)

This guide walks you through deploying a containerized FastAPI application on Amazon EKS, backed by a PostgreSQL RDS database and integrated with S3 using IAM Roles for Service Accounts (IRSA). It covers everything from cluster provisioning and networking to secure credential management, image delivery via ECR, and debugging common pitfalls.

Whether you're learning Kubernetes on AWS or setting up a production-ready baseline, this README provides a practical, step-by-step workflow with real commands and troubleshooting insights.

## Setup AWS account
```sh
aws configure

aws configure list

aws sts get-caller-identity

```

## EKS

```sh
# 1) set environmental vars
source .env

# 2) create cluster (takes ~15 min)
eksctl create cluster \
  --name $CLUSTER_NAME \
  --region $AWS_REGION \
  --nodes 2 \
  --node-type t3.small \
  --managed \
  --spot


# 3) connect kubectl to cluster
aws eks update-kubeconfig --region $AWS_REGION --name $CLUSTER_NAME

# 4) verify
eksctl get cluster

aws eks list-clusters --region $AWS_REGION
# aws eks list-clusters --profile my-profile
kubectl get nodes

kubectl config current-context

aws eks list-addons --cluster-name $CLUSTER_NAME
# {
#     "addons": [
#         "coredns",
#         "kube-proxy",
#         "metrics-server",
#         "vpc-cni"
#     ]
# }

```

### RDS PG

```sh
# find the VPC used by the cluster:
export VPC_ID=$(aws eks describe-cluster \
  --name $CLUSTER_NAME \
  --region $AWS_REGION \
  --query "cluster.resourcesVpcConfig.vpcId" \
  --output text)

echo $VPC_ID

# create a security group for the DB:
export DB_SG_ID=$(aws ec2 create-security-group \
  --group-name ${APP_NAME}-rds-sg \
  --description "RDS access for EKS app" \
  --vpc-id $VPC_ID \
  --region $AWS_REGION \
  --query "GroupId" \
  --output text)

echo $DB_SG_ID

# get EKS subnet IDs:
# aws ec2 describe-subnets \
#   --subnet-ids $(aws eks describe-cluster \
#     --name $CLUSTER_NAME \
#     --region $AWS_REGION \
#     --query "cluster.resourcesVpcConfig.subnetIds" \
#     --output text) \
#   --region $AWS_REGION \
#   --query '
#     Subnets[].{
#       Subnet: SubnetId,
#       CIDR: CidrBlock,
#       AZ: AvailabilityZone,
#       MapPublicIp: MapPublicIpOnLaunch
#     }' \
#   --output table

# get the private subnets:
export SUBNET_IDS=$(aws ec2 describe-subnets \
  --subnet-ids $(aws eks describe-cluster \
    --name $CLUSTER_NAME \
    --region $AWS_REGION \
    --query "cluster.resourcesVpcConfig.subnetIds" \
    --output text) \
  --region $AWS_REGION \
  --query "Subnets[?MapPublicIpOnLaunch==\`false\`].SubnetId" \
  --output text)

echo "Private EKS subnets:"
echo $SUBNET_IDS

DB_SUBNET_GROUP_NAME=RDS_FASTAPI_EKS
aws rds create-db-subnet-group \
  --db-subnet-group-name $DB_SUBNET_GROUP_NAME \
  --db-subnet-group-description "RDS subnet group for EKS app" \
  --subnet-ids $SUBNET_IDS \
  --region $AWS_REGION

# create DB instance in that subnet group (takes ~5min)
aws rds create-db-instance \
  --db-instance-identifier "$DB_INSTANCE_ID" \
  --db-instance-class "$DB_INSTANCE_CLASS" \
  --engine postgres \
  --engine-version 17.9 \
  --allocated-storage 20 \
  --storage-type gp3 \
  --master-username "$DB_USER" \
  --master-user-password "$DB_PASSWORD" \
  --db-name "$DB_NAME" \
  --db-subnet-group-name "$DB_SUBNET_GROUP_NAME" \
  --vpc-security-group-ids "$DB_SG_ID" \
  --no-publicly-accessible \
  --backup-retention-period 0 \
  --region "$AWS_REGION"

# wait until it becomes available
aws rds wait db-instance-available \
  --db-instance-identifier $DB_INSTANCE_ID \
  --region $AWS_REGION


# get the endpoint
export DB_HOST=$(aws rds describe-db-instances \
  --db-instance-identifier $DB_INSTANCE_ID \
  --region $AWS_REGION \
  --query "DBInstances[0].Endpoint.Address" \
  --output text)

# save this to `db.env`
echo $DB_HOST

```

Allow EKS nodes to reach the DB
```sh
# get the cluster's security group
export EKS_CLUSTER_SG=$(aws eks describe-cluster \
  --name $CLUSTER_NAME \
  --region $AWS_REGION \
  --query "cluster.resourcesVpcConfig.clusterSecurityGroupId" \
  --output text)

echo $EKS_CLUSTER_SG

# authorize inbound Postgres traffic from that security group:
aws ec2 authorize-security-group-ingress \
  --group-id $DB_SG_ID \
  --protocol tcp \
  --port 5432 \
  --source-group $EKS_CLUSTER_SG \
  --region $AWS_REGION

```

Store DB settings in a Kubernetes Secret

```sh
kubectl create configmap db-config \
  --from-env-file=db.env \
  --dry-run=client -o yaml > db-configmap.yaml

kubectl create configmap s3-config \
  --from-env-file=s3.env \
  --dry-run=client -o yaml > s3-configmap.yaml

kubectl apply -f db-configmap.yaml
kubectl apply -f s3-configmap.yaml

# verify
kubectl get cm
kubectl describe cm db-config
```

⚠️ **the values are base64 encoded, NOT encrypted.**
```sh
kubectl create secret generic app-db-secret \
  --from-literal=DB_PASSWORD="$DB_PASSWORD"

kubectl get secret

# check which values exist: (in data: section)
kubectl get secret app-db-secret -o yaml

# check the db password
kubectl get secret app-db-secret -o jsonpath="{.data.DB_PASSWORD}" | base64 -d
echo 'RG9lc1RoaXNTbWVsbEZ1bm55VG9Zb3U/' | base64 -d


# decode all values at once:
kubectl get secret app-db-secret -o json | jq -r '.data | map_values(@base64d)'
# {
#   "DB_PASSWORD": "TopSecretDBPassword!"
# }
```

## ECR

Login
```sh
aws ecr get-login-password --region $AWS_REGION | \
docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com


# ⚠️ equivalent (less secure) version
# 👉 Problem: password ends up in shell history / process list.
docker login \
  --username AWS \
  --password "$(aws ecr get-login-password --region $AWS_REGION)" \
  $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com

```

Create ECR repo. Build, tag & push the app image:
```sh
aws ecr create-repository --repository-name $APP_NAME --region $AWS_REGION

docker build -t $APP_NAME .
# docker run --rm -it --entrypoint sh $APP_NAME

docker tag $APP_NAME:latest $IMAGE_URI

docker push $IMAGE_URI
# docker push $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$APP_NAME:latest
```

## Deploy the App to EKS

```sh
# sed "s|REPLACE_ME|$IMAGE_URI|g" k8s.yaml.bak > k8s.yaml
# verify the image uri is correct:
cat k8s.yaml | grep image:

# Make sure the app image uri, the sa ... are set correctly:
# 📝 set the `serviceAccountName: pod-ident-sa`
kubectl apply -f k8s.yaml

```
The deployment fails:
```sh
kubectl describe deploy fastapi-eks-demo
# Events:
#   Type    Reason             Age    From                   Message
#   ----    ------             ----   ----                   -------
#   Normal  ScalingReplicaSet  5m42s  deployment-controller  Scaled up replica set fastapi-eks-demo-7cb8677b6b from 0 to 1

kubectl describe rs fastapi-eks-demo-7cb8677b6b
# Events:
#   Type     Reason        Age                  From                   Message
#   ----     ------        ----                 ----                   -------
#   Warning  FailedCreate  37s (x17 over 6m5s)  replicaset-controller  Error creating: pods "fastapi-eks-demo-7cb8677b6b-" is forbidden: error looking up service account default/irsa-sa: serviceaccount "irsa-sa" not found
```


### service account
A `ServiceAccount` in a Pod defines what identity the Pod uses to talk to the Kubernetes API. Pods don't automatically have meaningful permissions by default (the default ServiceAccount is too limited). A **ServiceAccount + RBAC** controls what the Pod can do (e.g., read secrets, list pods).

```sh
# make sure AWS_ACCOUNT_ID & IAM_ROLE_NAME are saved as env. variable.
envsubst < irsa-sa.tpl.json > sa.yaml
kubectl apply -f sa.yaml

kubectl rollout status deployment/fastapi-app
# deployment "fastapi-app" successfully rolled out

kubectl rollout restart deploy/fastapi-app
# deployment.apps/fastapi-app restarted

```

Make sure the DB-related environmental variables are set properly:
```sh
k exec -it fastapi-app-d858c784d-5mwqm -- env | grep DB_
# DB_PORT=5432
# DB_PASSWORD=DoesThisSmellFunnyToYou?
# DB_NAME=appdb
# DB_USER=appuser
# DB_HOST=fastapi-demo-db.ckacvvm9jt6m.us-east-1.rds.amazonaws.com
```
⚠️❌⚠️
Kubernetes env vars are not secret-safe by default — they’re just injected into the process.
Anyone with `kubectl exec` or sometimes even `kubectl describe pod` can potentially access sensitive values.

## Test the App:
```sh
kubectl get svc fastapi-demo
# NAME               TYPE       CLUSTER-IP     EXTERNAL-IP   PORT(S)        AGE
# fastapi-demo       NodePort   10.100.1.187   <none>        80:30605/TCP   13m

# 📝 MAKE SURE TO add a rule to give inbound access to the NodePort (above: 30605) for the worker node's SG.
# You can do that in AWS console 
H_=NODE_PUBLIC_IP:NodePort

curl -i $H_/healthz
curl -i $H_/db-check

curl -X POST "$H_/visit" \
  -H "accept: application/json" \
  -d ''

curl -X 'GET' \
  "$H_/visits" \
  -H 'accept: application/json'
```



## OIDC 
```sh
curl -i $H_/s3-check
# at this moment you'll get the following ERROR 🔽
```
When calling the `AssumeRoleWithWebIdentity` operation:
No `OpenIDConnect provider` found in your account for `https://oidc.eks.us-east-1.amazonaws.com/id/34B091E29D4970E8C0D3C8803C9DB236"`

**IRSA** needs an `IAM OIDC provider` associated with the cluster issuer URL.
```sh
# get the EKS-managed OIDC issuer URL for the cluster
aws eks describe-cluster \
  --name $CLUSTER_NAME \
  --region $AWS_REGION \
  --query "cluster.identity.oidc.issuer" \
  --output text

# your AWS account currently has no IAM OIDC provider configured for this EKS cluster YET.
# EKS cluster OIDC issuer exists ✅
# IAM trust provider for it does not exist yet ❌
# IRSA will not work until you create it ❌
aws iam list-open-id-connect-providers
# {
#     "OpenIDConnectProviderList": []
# }

eksctl utils associate-iam-oidc-provider \
  --cluster $CLUSTER_NAME \
  --region $AWS_REGION \
  --approve

aws iam list-open-id-connect-providers
# {
#     "OpenIDConnectProviderList": [
#         {
#             "Arn": "arn:aws:iam::854912240456:oidc-provider/oidc.eks.us-east-1.amazonaws.com/id/D7A48C67FCC7BB23C38D1CD2DE498907"
#         }
#     ]
# }


```

### Trust Policy
```sh
# get the cluster issuer URL (used in the trust policy 👇)
# 📝 this URL is the same as what was shown when we got the error 🔼
export OIDC_PROVIDER=$(aws eks describe-cluster \
  --name $CLUSTER_NAME \
  --region $AWS_REGION \
  --query "cluster.identity.oidc.issuer" \
  --output text | sed 's#^https://##')

echo $OIDC_PROVIDER
# oidc.eks.us-east-1.amazonaws.com/id/B988306817B67B33A9034AEB33066D4E

```

For `IRSA` to work, the remaining pieces are:
- the `IAM role` ARN on the `service account` must be the real role in the same account
- the `role trust policy` must trust that exact OIDC provider and that exact service account subject
- the `role permissions policy` must allow the S3 actions your app uses

```sh
# crate the IAM role
# the role ARN will go to the `irsa-sa.yml`
# ⚠️ don't forget to provider in the `irsa-trust-policy.json` with the cluster's actual OIDC issuer 👆
# the region & the id

# make sure AWS_ACCOUNT_ID & OIDC_PROVIDER are saved as env. variable.
envsubst < irsa-trust-policy.tpl.json > trust-policy.json

aws iam create-role \
  --role-name $IAM_ROLE_NAME \
  --assume-role-policy-document file://trust-policy.json

# create the S3 permissions policy
# ⚠️ Make sure it gives permission to the right S3 bucket.
export S3_BUCKET=fastapi-eks-demo-ijk
envsubst < s3-policy.json.tpl > s3-policy.json

aws iam create-policy \
  --policy-name $IAM_POLICY_NAME \
  --policy-document file://s3-policy.json

# attach the policy
aws iam attach-role-policy \
  --role-name $IAM_ROLE_NAME \
  --policy-arn arn:aws:iam::$AWS_ACCOUNT_ID:policy/$IAM_POLICY_NAME

# verify the trust policy on the role:
aws iam get-role --role-name $IAM_ROLE_NAME \
  --query 'Role.AssumeRolePolicyDocument' \
  --output json

# # after fixing the Kubernetes service account, apply it:
# # the `role-arn` in annotations should be the arn of the role we created above.
# kubectl apply -f sa.yaml

# verify:
kubectl get sa irsa-sa -n default -o yaml
# The annotation must now show:
## eks.amazonaws.com/role-arn: arn:aws:iam::854912240456:role/FastApiS3Role
```

Pods need to be restarted after fixing the service account/role wiring so they get fresh credentials.
```sh
kubectl rollout restart deployment/fastapi-eks-demo

kubectl rollout status deployment/fastapi-eks-demo

```
final check:
```sh
curl -i "$H_/s3-check"

echo "hello from eks" > sample11.txt
curl -i -X POST "$H_/upload" -F "file=@sample11.txt"

curl -Lo casino.jpg https://www.jamesbond.de/wp-content/uploads/2026/03/21-casinoroyale-deutsches-poster.jpg
curl -i -X POST "$H_/upload" -F "file=@./casino.jpg"

curl -i "$H_/files"

curl -X POST "$H_/visit" \
  -H "accept: application/json" \
  -d ''

```

### investiage DB
Launch a temporary psql client pod in the cluster:
```sh
kubectl run psql-client \
  --rm -it \
  --image=postgres:17 \
  --restart=Never \
  --env="PGPASSWORD=$DB_PASSWORD" \
  --command -- psql -h $DB_HOST -p 5432 -U $DB_USER -d $DB_NAME
```

Verify the data:
```sql
SELECT * FROM visits;
/*
appdb=> select * from visits;
 id |          created_at           
----+-------------------------------
  1 | 2026-04-09 00:14:17.394284+00
  2 | 2026-04-09 00:40:04.354593+00
  3 | 2026-04-09 00:44:18.226821+00
(3 rows)
*/


SELECT * FROM files;
/*
appdb=> select * from files;
 id | original_filename |            s3_key             | content_type | size_bytes |          created_at           |          deleted_at           
----+-------------------+-------------------------------+--------------+------------+-------------------------------+-------------------------------
  2 | casino.jpg        | uploads/3156f0ab-casino.jpg   | image/jpeg   |      32190 | 2026-04-09 00:37:17.683319+00 | 
  1 | sample11.txt      | uploads/f150a5cb-sample11.txt | text/plain   |         15 | 2026-04-09 00:33:33.876185+00 | 2026-04-09 00:42:17.190389+00
*/
```

## Clean up
❌❌❌ DO NOT FORGET TO DELETE the resources ❌❌❌
Run this cleanup step to remove temporary resources and avoid unnecessary cloud costs after testing or deployment. Deleting the cluster ensures no leftover infrastructure continues running, which could lead to unexpected charges or resource conflicts later.

```sh
# chmod +x delete-rds.sh
./delete-rds.sh

eksctl delete cluster --name $CLUSTER_NAME --region $AWS_REGION

```


## DDX
Debugging common pitfalls: 

```sh
kubectl get sa fastapi-eks-demo -n default -o yaml
aws iam get-role --role-name fastapi-eks-demo-s3-role --query 'Role.AssumeRolePolicyDocument' --output json
aws iam list-attached-role-policies --role-name fastapi-eks-demo-s3-role

```

An error occurred (AccessDenied) when calling the AssumeRoleWithWebIdentity operation: Not authorized to perform sts:AssumeRoleWithWebIdentity
```sh
# fix the oidc issuer url in the trus-policy & update the role:
aws iam update-assume-role-policy \
  --role-name fastapi-eks-demo-s3-role \
  --policy-document file://trust-policy.json

# verify
aws iam get-role \
  --role-name fastapi-eks-demo-s3-role \
  --query 'Role.AssumeRolePolicyDocument' \
  --output json

# should match with what this gives you:
aws eks describe-cluster \
  --name $CLUSTER_NAME \
  --region $AWS_REGION \
  --query "cluster.identity.oidc.issuer" \
  --output text

# restart the workload:
kubectl rollout restart deployment/fastapi-eks-demo

# test STS from inside the pod
kubectl exec deploy/fastapi-eks-demo -- python - <<'PY'
import boto3
print(boto3.client("sts").get_caller_identity())
PY
```
