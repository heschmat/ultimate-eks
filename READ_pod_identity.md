# 🚀 Kubernetes → AWS S3 Access with **EKS Pod Identity**

### A step-by-step guide for secure credentialless AWS access from pods

This README is a step-by-step guide to giving a Kubernetes pod secure access to AWS (specifically S3) using **EKS Pod Identity**, without hardcoding credentials.

---

# 🧠 Big Picture

It explains how a pod (our FastAPI app) can safely access AWS resources by mapping:

* a Kubernetes identity (**ServiceAccount**)
* to an AWS identity (**IAM Role**)

👉 **The key idea:**
**The app never stores AWS keys — credentials are generated dynamically and injected at runtime.**

---

# 🔄 The Core Flow

```md
FastAPI Pod
   ↓
Kubernetes ServiceAccount
   ↓
EKS Pod Identity Association
   ↓
IAM Role
   ↓
STS temporary credentials
   ↓
S3 access
```

### What happens behind the scenes

* The pod runs with a ServiceAccount
* AWS EKS maps that to an IAM role
* AWS generates temporary credentials (via STS)
* Our app (via boto3) uses them automatically

---

# 1️⃣ Create the Kubernetes Service Account

```sh
# This is the identity inside Kubernetes (this pod is running as user `pod-ident-sa`)
# 📢 ZERO AWS permissions YET.
kubectl apply -f pod-ident-sa.yaml
```

---

# 2️⃣ Install the EKS Pod Identity Agent

## 📌 Pod Identity Addon Setup

```sh
aws eks create-addon \
  --cluster-name $CLUSTER_NAME \
  --addon-name eks-pod-identity-agent \
  --region $AWS_REGION


# verify (one per node)
kubectl get pods -n kube-system | grep eks-pod-identity-agent

aws eks list-addons --cluster-name $CLUSTER_NAME
# {
#     "addons": [
#         "coredns",
#         "eks-pod-identity-agent", ✅
#         "kube-proxy",
#         "metrics-server",
#         "vpc-cni"
#     ]
# }
```

> **NOTE:**
> The `Pod Identity Agent` runs on every node; it runs as a `DaemonSet` (one agent pod per worker node.)

```sh
kubectl get daemonset -n kube-system
# NAME                     DESIRED   CURRENT   READY   UP-TO-DATE   AVAILABLE   NODE SELECTOR   AGE
# aws-node                 1         1         1       1            1           <none>          68m
# eks-pod-identity-agent   1         1         1       1            1           <none>          48m ✅
# kube-proxy               1         1         1       1            1           <none>          68m
```

### 🎯 Its job

* detect pods on the node
* check their service account
* fetch temporary credentials from EKS Auth
* expose them to the AWS SDK

So **your app never handles long-term credentials**.

---

# 3️⃣ Create IAM Policy + IAM Role + Association

```sh
export S3_BUCKET=fastapi-eks-demo-ijk
envsubst < s3-policy.json.tpl > s3-policy.json

aws iam create-policy \
  --policy-name $IAM_POLICY_NAME \
  --policy-document file://s3-policy.json


# create the IAM role for Pod Identity
# This says: EKS Pod Identity is allowed to assume this role on behalf of pods.
# 📢 This is why `Pod Identity` is easier than `IRSA`. No `OIDC provider` setup needed.
aws iam create-role \
  --role-name $IAM_ROLE_NAME \
  --assume-role-policy-document file://pod-ident-trust.json

# attach the policy to the IAM role
aws iam attach-role-policy \
  --role-name $IAM_ROLE_NAME \
  --policy-arn arn:aws:iam::$AWS_ACCOUNT_ID:policy/$IAM_POLICY_NAME

aws eks list-pod-identity-associations \
  --cluster-name $CLUSTER_NAME \
  --region $AWS_REGION
# {
#     "associations": []
# }

# associate the IAM role with the service account
# This is the bridge between Kubernetes and AWS.
# translation 🪧:
# whenever a pod runs as namespace/name (specified in sa's metadata), EKS should give it credentials for $IAM_ROLE_NAME
# This mapping is stored in EKS itself.
# EKS injects environment variables into the pod automatically.
aws eks create-pod-identity-association \
  --cluster-name $CLUSTER_NAME \
  --namespace $CLUSTER_NS \
  --service-account pod-ident-sa \
  --role-arn arn:aws:iam::$AWS_ACCOUNT_ID:role/$IAM_ROLE_NAME \
  --region $AWS_REGION


# verify
aws eks list-pod-identity-associations --cluster-name $CLUSTER_NAME --region $AWS_REGION
# {
#     "associations": [
#         {
#             "clusterName": "fastapi-eks-demo",
#             "namespace": "default",
#             "serviceAccount": "pod-ident-sa",
#             "associationArn": "arn:aws:eks:us-east-1:854912240456:podidentityassociation/fastapi-eks-demo/a-z5jsgfv08qfy3nzgt",
#             "associationId": "a-z5jsgfv08qfy3nzgt" 👇
#         }
#     ]
# }

# get the association-id above:
# This is the direct proof that EKS has mapped the service account default/pod-ident-sa to the IAM role.
aws eks describe-pod-identity-association \
  --cluster-name $CLUSTER_NAME \
  --association-id a-z5jsgfv08qfy3nzgt \
  --region $AWS_REGION

# {
#     "association": {
#         "clusterName": "fastapi-eks-demo",
#         "namespace": "default",
#         "serviceAccount": "pod-ident-sa", ✅
#         "roleArn": "arn:aws:iam::854912240456:role/FastApiS3Role", ✅
#         "associationArn": "arn:aws:eks:us-east-1:854912240456:podidentityassociation/fastapi-eks-demo/a-z5jsgfv08qfy3nzgt",
#         "associationId": "a-z5jsgfv08qfy3nzgt",
#         "tags": {},
#         "createdAt": "2026-04-14T05:19:56.800000+00:00",
#         "modifiedAt": "2026-04-14T05:19:56.800000+00:00",
#         "disableSessionTags": false
#     }
# }
```

---

# 4️⃣ Deploy the FastAPI App with the Correct Service Account

```sh
# 📝 Make sure you set the `serviceAccountName: pod-ident-sa`
kubectl apply -f k8s.yaml
```

---

# 5️⃣ Verify Environment Variables Injected by EKS

With Pod Identity, EKS injects `AWS_CONTAINER_CREDENTIALS_FULL_URI` and `AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE` into pods, and the SDK uses those first.

```sh
kubectl exec -it deploy/fastapi-eks-demo -- env | grep AWS_
# AWS_STS_REGIONAL_ENDPOINTS=regional
# AWS_CONTAINER_CREDENTIALS_FULL_URI=http://169.254.170.23/v1/credentials
# AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE=/var/run/secrets/pods.eks.amazonaws.com/serviceaccount/eks-pod-identity-token
# AWS_DEFAULT_REGION=us-east-1
# AWS_REGION=us-east-1

# ✅ the pod received Pod Identity env vars
# ⚠️ If you don't see the top 4 env. variable, sth is wront.
```

---

# 6️⃣ Investigate the Injected Token

Investigate the token

```sh
# you should see a a JWT-like token:
kubectl exec -it deploy/fastapi-eks-demo -- cat /var/run/secrets/pods.eks.amazonaws.com/serviceaccount/eks-pod-identity-token
# eyJhbGciOiJSUzI1NiIsImtpZCI6...
```

---

# 7️⃣ Manually Test the Credential Endpoint

We can even call the credential endpoint manually:

```sh
kubectl exec -it deploy/fastapi-eks-demo -- sh

# you may need to install curl for the next command to work:
# apt-get update && apt-get install -y curl

curl -s \
  -H "Authorization: $(tr -d '\n' < "$AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE")" \
  "$AWS_CONTAINER_CREDENTIALS_FULL_URI"
# {
#   "AccessKeyId": "ASIA....",
#   "SecretAccessKey": "...",
#   "Token": "...",
#   "AccountId": "..."
#   "Expiration": "2026-04-12T4:25:00Z"
# }

# The above response comes from the Pod Identity Agent on the node, not directly from STS.
# This single curl command proves the whole auth chain works.
```

---

# 8️⃣ Confirm the IAM Role via boto3

This is the most important verification:

```sh
kubectl exec -it deploy/fastapi-eks-demo -- sh
# python
```

```py
Python 3.12.13 (main, Apr  7 2026, 02:23:40) [GCC 14.2.0] on linux
Type "help", "copyright", "credits" or "license" for more information.
>>> import boto3

>>>
>>> print(boto3.client("sts").get_caller_identity())
{'UserId': 'AROA4ODF5RNEAB6KHRZN6:eks-fastapi-de-fastapi-ek-6fc9926b-dbdd-477a-b4dd-4168b52364ac', 'Account': '854912240456', 'Arn': 'arn:aws:sts::854912240456:assumed-role/FastApiS3Role/eks-fastapi-de-fastapi-ek-6fc9926b-dbdd-477a-b4dd-4168b52364ac', ...}

# ✅✅✅ The pod is actually assuming the IAM role (FastApiS3Role)
```

Before we explain, you could simply use this command for all the above:

```sh
kubectl exec -it deploy/fastapi-eks-demo -- python -c "import boto3; print(boto3.client('sts').get_caller_identity())"
```

If in above code you see:

```text
'Arn': 'arn:aws:sts::854912240456:assumed-role/FastApiS3Role/...'
```

(the IAM role we created above), that's a confirmation that pod identity association is correct and boto3 is using the right role.

Now we can test the app:
```sh
curl -X 'POST' \
  'http://98.86.175.6:30145/upload' \
  -H 'accept: application/json' \
  -H 'Content-Type: multipart/form-data' \
  -F 'file=@dark-knight.jpg;type=image/jpeg'

# get a temp. download url for the file
curl -X 'GET' \
  'http://98.86.175.6:30145/files/1/download-url?expires_in=900' \
  -H 'accept: application/json'
# {"file_id":1,"filename":"dark-knight.jpg","expires_in":900,"download_url":"https://fastapi-eks-demo-ijk.s3.amazonaws.com/uploads/02ecd804-dark-knight.jpg?AWSAccessKeyId=ASIA4ODF5RNECFFQVRSH&Signature=Frrr....}
```

---

# ⚠️ Common Failure Mode

On the other hand if you see sth like:

```text
'Arn': 'arn:aws:sts::854912240456:assumed-role/eksctl-fastapi-demo-nodegroup-ng-c-NodeInstanceRole-JnCA7KpueYRk/i-03f544ac32ed2ec2a'
```

i.e., the node role — something went wrong.

The pod is assuming `the node instance role` means Pod Identity is **NOT being selected at credential resolution time**.

That means boto3 is falling back to the node’s IAM credentials, not pod-scoped credentials.

### 💡 One common reason

One reason for this issue could be that the pod has been created before the association/service account wiring was in effect.

---

# 🧾 Key Takeaways

* **Kubernetes service account** = who the pod is in Kubernetes
* **IAM role** = what the pod can do in AWS
* **Pod identity association** = mapping between them

---

# ⚔️ IRSA vs. Pod Identity

ISRA => every pod calls STS directly
Pod Identity => pod → node agent → EKS Auth → STS

| Feature            | IRSA                | Pod Identity            |
| ------------------ | ------------------- | ----------------------- |
| Credential flow    | Pod → STS           | Pod → Agent → EKS → STS |
| Setup complexity   | Needs OIDC provider | Simpler                 |
| Calls STS directly | Yes                 | No                      |
