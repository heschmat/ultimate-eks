

```sh
docker compose pull

docker compose run --rm --entrypoint sh tf
```


```sh

# List EKS clusters
aws eks list-clusters --region $AWS_REGION

# Describe cluster
aws eks describe-cluster \
  --name $CLUSTER_NAME \
  --region $AWS_REGION

# Update kubeconfig
aws eks update-kubeconfig \
  --name $CLUSTER_NAME \
  --region $AWS_REGION

kubectl get events -A --sort-by=.lastTimestamp

```


## deploy sample nginx

```sh
kubectl create deployment nginx --image=nginx:stable

kubectl expose deployment nginx \
  --port=80 \
  --target-port=80 \
  --type=LoadBalancer

kubectl get svc nginx -w

curl http://a1b2c3d4.elb.amazonaws.com

kubectl get events -A --sort-by=.lastTimestamp

aws elbv2 describe-load-balancers --region $AWS_REGION
# "Scheme": "internet-facing" vs. "internal"


aws eks describe-cluster \
  --name <cluster-name> \
  --region us-east-1 \
  --query 'cluster.resourcesVpcConfig.vpcId'


# another possibility is that your Terraform VPC module is only exposing private subnets to Kubernetes.
aws ec2 describe-subnets \
  --filters Name=vpc-id,Values=<vpc-id> \
  --query 'Subnets[*].[SubnetId,Tags]' \
  --output table

aws ec2 describe-subnets \
  --filters Name=vpc-id,Values=vpc-02c951184be1eb414 \
  --query 'Subnets[*].{
      Subnet:SubnetId,
      AZ:AvailabilityZone,
      PublicIp:MapPublicIpOnLaunch,
      Tags:Tags
  }' \
  --output json
```