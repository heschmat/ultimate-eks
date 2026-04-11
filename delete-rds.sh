#!/usr/bin/env bash
set -euo pipefail

: "${AWS_REGION:?AWS_REGION is required}"
: "${DB_INSTANCE_ID:?DB_INSTANCE_ID is required}"
: "${APP_NAME:?APP_NAME is required}"

DB_SUBNET_GROUP_NAME="${DB_SUBNET_GROUP_NAME:-RDS_FASTAPI_EKS}"
DB_SG_NAME="${APP_NAME}-rds-sg"

db_exists() {
  aws rds describe-db-instances \
    --db-instance-identifier "$DB_INSTANCE_ID" \
    --region "$AWS_REGION" \
    >/dev/null 2>&1
}

subnet_group_exists() {
  aws rds describe-db-subnet-groups \
    --db-subnet-group-name "$DB_SUBNET_GROUP_NAME" \
    --region "$AWS_REGION" \
    >/dev/null 2>&1
}

sg_id_by_name() {
  aws ec2 describe-security-groups \
    --filters "Name=group-name,Values=$DB_SG_NAME" \
    --region "$AWS_REGION" \
    --query "SecurityGroups[0].GroupId" \
    --output text 2>/dev/null || true
}

if db_exists; then
  DB_SG_ID=$(aws rds describe-db-instances \
    --db-instance-identifier "$DB_INSTANCE_ID" \
    --region "$AWS_REGION" \
    --query "DBInstances[0].VpcSecurityGroups[0].VpcSecurityGroupId" \
    --output text 2>/dev/null || true)

  aws rds modify-db-instance \
    --db-instance-identifier "$DB_INSTANCE_ID" \
    --no-deletion-protection \
    --apply-immediately \
    --region "$AWS_REGION" >/dev/null 2>&1 || true

  aws rds delete-db-instance \
    --db-instance-identifier "$DB_INSTANCE_ID" \
    --skip-final-snapshot \
    --delete-automated-backups \
    --region "$AWS_REGION"

  aws rds wait db-instance-deleted \
    --db-instance-identifier "$DB_INSTANCE_ID" \
    --region "$AWS_REGION"
else
  echo "DB instance $DB_INSTANCE_ID does not exist, skipping."
  DB_SG_ID="$(sg_id_by_name)"
fi

if subnet_group_exists; then
  aws rds delete-db-subnet-group \
    --db-subnet-group-name "$DB_SUBNET_GROUP_NAME" \
    --region "$AWS_REGION"
else
  echo "DB subnet group $DB_SUBNET_GROUP_NAME does not exist, skipping."
fi

if [[ -n "${DB_SG_ID:-}" && "$DB_SG_ID" != "None" ]]; then
  aws ec2 delete-security-group \
    --group-id "$DB_SG_ID" \
    --region "$AWS_REGION" || echo "Security group could not be deleted."
else
  echo "Security group not found, skipping."
fi

echo "Cleanup complete."
