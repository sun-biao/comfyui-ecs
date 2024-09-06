## Getting Started

### Prepare the AWS environment

For the sake of reproducability and consistency, we recommend using [AWS Cloud9](https://docs.aws.amazon.com/cloud9/latest/user-guide/welcome.html) IDE for deploying and testing this solution.

ℹ️ You can use your local development environment, but you will need to **make sure that you have AWS CLI, AWS CDK and Docker properly setup**. Additionally, if you're building your docker image using apple chips (M1, M2, etc.) then you need to use the Docker ```docker build --platform linux/amd64 .``` command.

<details>
<summary>Click to see environment setup with Cloud9</summary>

1. Login to AWS Console
2. Navigate to Cloud9
3. Create Environment with following example details:
    - Name: Give your Dev Environment a name of choice
    - Instance Type: t2.micro (default) got a free-tier
    - Platform: Ubuntu Server 22.04 LTS
    - Timeout: 30 minutes
    - Other settings can be configured with the default values
4. Create and open environment
5. resize disk space
    ```bash
    curl -o resize.sh https://raw.githubusercontent.com/aws-samples/semantic-search-aws-docs/main/cloud9/resize.sh
    chmod +x ./resize.sh
    ./resize.sh 100
    ```
6. git clone <enter this repo URL here>
7. cd into new directory
</details>

<details>
<summary>Click to see environment setup with Local environment</summary>

If you do not have AWS CLI, follow [AWS CLI Install Guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)

If you do not have CDK, follow [CDK Start Guide](https://docs.aws.amazon.com/cdk/v2/guide/getting_started.html)

If you do not have Docker follow [Docker Install Guide](https://docs.docker.com/engine/install/)

If you haven't setup AWS CLI after installation, execute the following commands on your local environment:

```bash
aws configure
```

When prompted, enter your AWS Access Key ID, Secret Access Key, and then the default region name (eg. us-east-1). You can leave the output format field as default or specify it as per your preference.
</details>

After setting up environment, set environment variable below. These variables will be used in many of the commands below.

```bash
export AWS_DEFAULT_REGION=<aws_region> # e.g. "us-east-1", "eu-central-1"
export AWS_DEFAULT_ACCOUNT=<your_account_id> # e.g. 123456789012
export ECR_REPO_NAME="comfyui-rick"
```

### Build & push docker image to ECR

You could build & reference your docker image in CDK directly, but we're using docker build and push the image to ECR, that we don't need to build the docker image with every CDK deployment. Additionally, the image is getting scanned
for vulnerabilites as soon as you push the image to ECR. You can achieve this as following:

1. Create an ECR repository and login
```
aws ecr create-repository --repository-name $ECR_REPO_NAME --image-scanning-configuration scanOnPush=true
```
2. Login to ECR
```
aws ecr get-login-password --region $AWS_DEFAULT_REGION | docker login --username AWS --password-stdin $AWS_DEFAULT_ACCOUNT.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com/$ECR_REPO_NAME
```
3. Build docker image (make sure you're in the same directory as your dockerfile)
```
docker build -t comfyui .
# or alternatively if you are using M1 / M2 / ... Mac
docker build --platform linux/amd64 -t comfyui-rick .
```
4. Tag and push docker image to ECR
```
docker tag comfyui-rick:latest $AWS_DEFAULT_ACCOUNT.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com/$ECR_REPO_NAME:latest
docker push $AWS_DEFAULT_ACCOUNT.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com/$ECR_REPO_NAME:latest
```

### Deploying ComfyUI

1. (First time only) Install Required Dependency
```python
python -m pip install -r requirements.txt
```
2. (First time only) If you use CDK in your first time in an account/region, then you need to run following command to bootstrap your account. For subsequent deployments this step is not required anymore
```bash
cdk bootstrap
```
3. Deploy ComfyUI to your default AWS account and region
```bash
cdk deploy
```

Depending on your custom_nodes and extenstions in the dockerfile, the deployment will take approx. 8-10 minutes to have ComfyUI ready
