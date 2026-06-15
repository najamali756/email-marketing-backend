# aws ecr get-login-password --region us-east-1 --profile techno | docker login --username AWS --password-stdin 796973472333.dkr.ecr.us-east-1.amazonaws.com
#

# docker build -t 796973472333.dkr.ecr.us-east-1.amazonaws.com/marketing-django-backend:latest .
# docker push 796973472333.dkr.ecr.us-east-1.amazonaws.com/marketing-django-backend:latest
# aws ecs update-service --cluster cluster-3 --service ginkgo-logistics-backend-service --force-new-deployment --profile ginkgo
