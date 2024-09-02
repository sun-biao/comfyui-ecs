from aws_cdk import (
    # Duration,
    aws_ecs as ecs,
    aws_ec2 as ec2,
    aws_ecr as ecr,
    aws_logs as logs,
    aws_s3 as s3,
    aws_iam as iam,
    aws_autoscaling as autoscaling,
    aws_elasticloadbalancingv2 as elbv2,
    aws_elasticloadbalancingv2_actions as elb_actions,
    aws_elasticloadbalancingv2_targets as targets,
    aws_events as events,
    aws_events_targets as event_targets,
    Stack,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cw_actions,
    Duration,
    RemovalPolicy,
    CustomResource,
    # aws_sqs as sqs,
    CfnOutput
)
from constructs import Construct
import json, hashlib
from cdk_nag import NagSuppressions

# with open(
#     "./cdk_comfyui_sunbiao/cert.json",
#     "r",
# ) as file:
#     config = json.load(file)

class CdkComfyuiSunbiaoStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # The code that defines your stack goes here

        # example resource
        # queue = sqs.Queue(
        #     self, "CdkComfyuiSunbiaoQueue",
        #     visibility_timeout=Duration.seconds(300),
        # )

                # Setting
        unique_input = f"{self.account}-{self.region}-comfyui"
        unique_hash = hashlib.sha256(unique_input.encode('utf-8')).hexdigest()[:10]
        suffix = unique_hash.lower()
        
              # Get context
        autoScaleDown = self.node.try_get_context("autoScaleDown")
        if autoScaleDown is None:
            autoScaleDown = True

        cheapVpc = self.node.try_get_context("cheapVpc") or False
        
        scheduleAutoScaling = self.node.try_get_context("scheduleAutoScaling") or False
        timezone = self.node.try_get_context("timezone") or "UTC"
        scheduleScaleUp = self.node.try_get_context("scheduleScaleUp") or "0 9 * * 1-5"
        scheduleScaleDown = self.node.try_get_context("scheduleScaleDown") or "0 18 * * *"
        
        if cheapVpc:
            natInstance = ec2.NatProvider.instance_v2(
                instance_type=ec2.InstanceType("t4g.nano"),
                default_allowed_traffic=ec2.NatTrafficDirection.OUTBOUND_ONLY,
            )

        vpc = ec2.Vpc(
            self, "ComfyRickVPC",
            max_azs=2,  # Define the maximum number of Availability Zones
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24
                )
            ],
            nat_gateway_provider=natInstance if cheapVpc else None,
            gateway_endpoints={
                # ECR Image Layer
                "S3": ec2.GatewayVpcEndpointOptions(
                    service=ec2.GatewayVpcEndpointAwsService.S3
                )
            }
        )
        

        if cheapVpc:
            natInstance.security_group.add_ingress_rule(
                ec2.Peer.ipv4(vpc.vpc_cidr_block),
                ec2.Port.all_traffic(),
                "Allow NAT Traffic from inside VPC",
            )
            
        # Create ALB Security Group
        alb_security_group = ec2.SecurityGroup(
            self,
            "ALBSecurityGroup",
            vpc=vpc,
            description="Security Group for ALB",
            allow_all_outbound=True,
        )
        alb_security_group.add_ingress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(80),
            "Allow inbound traffic on port 443",
        )
        
        # Create Auto Scaling Group Security Group
        asg_security_group = ec2.SecurityGroup(
            self,
            "AsgSecurityGroup",
            vpc=vpc,
            description="Security Group for ASG",
            allow_all_outbound=True,
        )
        
        # EC2 Role for AWS internal use (if necessary)
        ec2_role = iam.Role(
            self,
            "EC2Role",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEC2FullAccess"), # check if less privilege can be given
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedEC2InstanceDefaultPolicy")
            ]
        )

        user_data_script = ec2.UserData.for_linux()
        user_data_script.add_commands("""
            #!/bin/bash
            REGION=$(curl -s http://169.254.169.254/latest/meta-data/placement/region) 
            docker plugin install rexray/ebs --grant-all-permissions REXRAY_PREEMPT=true EBS_REGION=$REGION
            systemctl restart docker
        """)
        
        
        # Create an Auto Scaling Group with two EBS volumes
        launchTemplate = ec2.LaunchTemplate(
            self,
            "Host",
            launch_template_name="ComfyLaunchTemplateRickHost",
            instance_type=ec2.InstanceType("g4dn.2xlarge"),
            machine_image=ecs.EcsOptimizedImage.amazon_linux2(
                hardware_type=ecs.AmiHardwareType.GPU
            ),
            role=ec2_role,
            security_group=asg_security_group,
            user_data=user_data_script,
            block_devices=[
                ec2.BlockDevice(
                    device_name="/dev/xvda",
                    volume=ec2.BlockDeviceVolume.ebs(volume_size=50,
                                                     encrypted=True)
                )
            ],
        )
        
        auto_scaling_group = autoscaling.AutoScalingGroup(
            self,
            "ASG",
            auto_scaling_group_name="ComfyRickASG",
            vpc=vpc,
            mixed_instances_policy=autoscaling.MixedInstancesPolicy(
                instances_distribution=autoscaling.InstancesDistribution(
                    on_demand_percentage_above_base_capacity=100,
                    on_demand_allocation_strategy=autoscaling.OnDemandAllocationStrategy.LOWEST_PRICE,
                ),
                launch_template=launchTemplate,
                launch_template_overrides=[
                    autoscaling.LaunchTemplateOverrides(instance_type=ec2.InstanceType("g4dn.2xlarge")),
                    autoscaling.LaunchTemplateOverrides(instance_type=ec2.InstanceType("g5.2xlarge")),
                    autoscaling.LaunchTemplateOverrides(instance_type=ec2.InstanceType("g6.2xlarge")),
                ],
            ),
            min_capacity=0,
            max_capacity=1,
            desired_capacity=1,
            new_instances_protected_from_scale_in=False,
        )
        
        auto_scaling_group.apply_removal_policy(RemovalPolicy.DESTROY)

        cpu_utilization_metric = cloudwatch.Metric(
            namespace='AWS/EC2',
            metric_name='CPUUtilization',
            dimensions_map={
                'AutoScalingGroupName': auto_scaling_group.auto_scaling_group_name
            },
            statistic='Average',
            period=Duration.minutes(1)
        )

        # Scale down to zero if no activity for an hour
        if autoScaleDown:
            # create a CloudWatch alarm to track the CPU utilization
            cpu_alarm = cloudwatch.Alarm(
                self,
                "CPUUtilizationAlarm",
                metric=cpu_utilization_metric,
                threshold=1,
                evaluation_periods=60,
                datapoints_to_alarm=60,
                comparison_operator=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD
            )
            scaling_action = autoscaling.StepScalingAction(
                self,
                "ScalingAction",
                auto_scaling_group=auto_scaling_group,
                adjustment_type=autoscaling.AdjustmentType.CHANGE_IN_CAPACITY,
                cooldown=Duration.seconds(120)
            )
            # Add scaling adjustments
            scaling_action.add_adjustment(
                adjustment=-1,  # scaling adjustment (reduce instance count by 1)
                upper_bound=1   # upper threshold for CPU utilization
            )
            scaling_action.add_adjustment(
                adjustment=0,   # No change in instance count
                lower_bound=1   # Apply this when the metric is above 2%
            )
            # Link the StepScalingAction to the CloudWatch alarm
            cpu_alarm.add_alarm_action(
                cw_actions.AutoScalingAction(scaling_action)
            )
            
        # Create an ECS Cluster
        cluster = ecs.Cluster(
            self, "ComfyUIRickCluster", 
            vpc=vpc, 
            cluster_name="ComfyUIRickCluster", 
            container_insights=True
        )
        
        # Create ASG Capacity Provider for the ECS Cluster
        capacity_provider = ecs.AsgCapacityProvider(
            self, "AsgCapacityProvider",
            auto_scaling_group=auto_scaling_group,
            enable_managed_scaling=False,  # Enable managed scaling
            enable_managed_termination_protection=False,  # Disable managed termination protection
            target_capacity_percent=100
        )
        
        cluster.add_asg_capacity_provider(capacity_provider)
        
        # Create IAM Role for ECS Task Execution
        task_exec_role = iam.Role(
            self,
            "ECSTaskExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                )
            ],
        )
        
        
        # ECR Repository
        ecr_repository = ecr.Repository.from_repository_name(
            self, 
            "comfyui-rick", 
            "comfyui-rick")
            

        # CloudWatch Logs Group
        log_group = logs.LogGroup(
            self,
            "LogGroup",
            log_group_name="/ecs/comfy-rick-ui",
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Docker Volume Configuration
        volume = ecs.Volume(
            name="ComfyUIVolumeRick",
            docker_volume_configuration=ecs.DockerVolumeConfiguration(
                scope=ecs.Scope.SHARED,
                driver="rexray/ebs",
                driver_opts={
                    "volumetype": "gp3",
                    "size": "250"  # Size in GiB
                },
                autoprovision=True
            )
        )
        
        task_definition = ecs.Ec2TaskDefinition(
            self,
            "TaskDef",
            network_mode=ecs.NetworkMode.AWS_VPC,
            task_role=task_exec_role,
            execution_role=task_exec_role,
            volumes=[volume]
        )

        # Add container to the task definition
        container = task_definition.add_container(
            "ComfyUIContainer",
            image=ecs.ContainerImage.from_ecr_repository(ecr_repository, "latest"),
            gpu_count=1,
            memory_reservation_mib=30720,
            cpu=7680,
            logging=ecs.LogDriver.aws_logs(stream_prefix="comfy-rick-ui", log_group=log_group),
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", "curl -f http://localhost:8181/system_stats || exit 1"],
                interval=Duration.seconds(15),
                timeout=Duration.seconds(10),
                retries=8,
                start_period=Duration.seconds(30)
            )
        )
        
        container.add_mount_points(
            ecs.MountPoint(
                container_path="/home/user/opt/ComfyUI",
                source_volume="ComfyUIVolumeRick",
                read_only=False
            )
        )

        # Port mappings for the container
        container.add_port_mappings(
            ecs.PortMapping(
                container_port=8181,
                host_port=8181,
                app_protocol=ecs.AppProtocol.http,
                name="comfyui-port-mapping",
                protocol=ecs.Protocol.TCP,
            )
        )
        
        # Create ECS Service Security Group
        service_security_group = ec2.SecurityGroup(
            self,
            "ServiceSecurityGroup",
            vpc=vpc,
            description="Security Group for ECS Service",
            allow_all_outbound=True,
        )

        # Allow inbound traffic on port 8181
        service_security_group.add_ingress_rule(
            ec2.Peer.security_group_id(alb_security_group.security_group_id),
            ec2.Port.tcp(8181),
            "Allow inbound traffic on port 8181",
        )
        
        # Create ECS Service
        service = ecs.Ec2Service(
            self,
            "ComfyUIService",
            service_name="ComfyUIService",
            cluster=cluster,
            task_definition=task_definition,
            capacity_provider_strategies=[
                ecs.CapacityProviderStrategy(
                    capacity_provider=capacity_provider.capacity_provider_name, weight=1
                )
            ],
            security_groups=[service_security_group],
            health_check_grace_period=Duration.seconds(30),
            min_healthy_percent=0,
        )

        # Application Load Balancer
        alb = elbv2.ApplicationLoadBalancer(
            self, "ComfyUIALB",
            vpc=vpc,
            load_balancer_name="ComfyUIRickALB",
            internet_facing=True,
            security_group=alb_security_group
        )

        # # Redirect Load Balancer traffic on port 80 to port 443
        # alb.add_redirect(
        #     source_protocol=elbv2.ApplicationProtocol.HTTP,
        #     source_port=80
        # )
        
        
        # Add target groups for ECS service
        ecs_target_group = elbv2.ApplicationTargetGroup(
            self,
            "EcsTargetGroupRick",
            port=8181,
            vpc=vpc,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            targets=[
                service.load_balancer_target(
                    container_name="ComfyUIContainer", container_port=8181
                )],
            health_check=elbv2.HealthCheck(
                enabled=True,
                path="/system_stats",
                port="8181",
                protocol=elbv2.Protocol.HTTP,
                healthy_http_codes="200",  # Adjust as needed
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                unhealthy_threshold_count=3,
                healthy_threshold_count=2,
            )
        )
        
        
        # Add listener to the Load Balancer on port 443
        listener = alb.add_listener(
            "Listener", 
            port=80, 
            protocol=elbv2.ApplicationProtocol.HTTP,
            default_action=elbv2.ListenerAction.forward([ecs_target_group])
        )
        
        
        NagSuppressions.add_resource_suppressions(
            [alb_security_group,asg_security_group,service_security_group,alb],
            suppressions=[
                {"id": "AwsSolutions-EC23",
                 "reason": "The Security Group and ALB needs to allow 0.0.0.0/0 inbound access for the ALB to be publicly accessible. Additional security is provided via Cognito authentication."
                },
                { "id": "AwsSolutions-ELB2",
                 "reason": "Adding access logs requires extra S3 bucket so removing it for sample purposes."},
            ],
            apply_to_children=True
        )
        
        NagSuppressions.add_resource_suppressions(
            [task_definition],
            suppressions=[
                {"id": "AwsSolutions-ECS2",
                 "reason": "Recent aws-cdk-lib version adds 'AWS_REGION' environment variable implicitly."
                },
            ],
            apply_to_children=True
        )
        NagSuppressions.add_resource_suppressions(
            [vpc],
            suppressions=[
                {"id": "AwsSolutions-EC28",
                "reason": "NAT Instance does not require autoscaling."
                },
                {"id": "AwsSolutions-EC29",
                "reason": "NAT Instance does not require autoscaling."
                },
            ],
            apply_to_children=True
        )

        CfnOutput(self, "alb address", value=alb.load_balancer_dns_name)