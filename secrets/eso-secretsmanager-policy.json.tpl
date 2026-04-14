{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadFastApiDbSecret",
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue",
        "secretsmanager:DescribeSecret"
      ],
      "Resource": "arn:aws:secretsmanager:${AWS_REGION}:${AWS_ACCOUNT_ID}:secret:${SECRET_ID}"
    }
  ]
}
