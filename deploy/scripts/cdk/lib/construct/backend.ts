import { Duration } from 'aws-cdk-lib'
import { Construct } from 'constructs';
import {
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecr as ecr,
    aws_rds as rds,
    aws_servicediscovery as servicediscovery,
    aws_iam as iam,
    aws_logs as logs,
    aws_elasticloadbalancingv2 as elb,
} from 'aws-cdk-lib';

interface BackEndProps {
  cluster: ecs.Cluster
  ecsBackSG:ec2.SecurityGroup
  ecrBackEndRepository:ecr.Repository
  backendTaskRole: iam.Role;
  backendTaskExecutionRole: iam.Role;
  backendLogGroup: logs.LogGroup;
  cloudmapNamespace: servicediscovery.PrivateDnsNamespace;
  rdsCluster:rds.DatabaseCluster
  alb:elb.IApplicationLoadBalancer
  arch:ecs.CpuArchitecture
}

export class BackEndCluster extends Construct {
  readonly backendServiceName: string 
  
  constructor(scope: Construct, id: string, props:BackEndProps) {
    super(scope, id)
    const containerPort = 7860
    // Secrets ManagerからDB認証情報を取ってくる
    const secretsDB = props.rdsCluster.secret!;

    // Create Backend Fargate Service
    const backendTaskDefinition = new ecs.FargateTaskDefinition(
      this,
      'BackEndTaskDef',
      {
          memoryLimitMiB: 512,
          cpu: 256,
          executionRole: props.backendTaskExecutionRole,
          runtimePlatform:{
            operatingSystemFamily: ecs.OperatingSystemFamily.LINUX,
            cpuArchitecture: props.arch,
          },
          taskRole: props.backendTaskRole,
      }
    );
    backendTaskDefinition.addContainer('backendContainer', {
      image: ecs.ContainerImage.fromEcrRepository(props.ecrBackEndRepository, "latest"),
      containerName:'langflow-back-container',
      logging: ecs.LogDriver.awsLogs({
        streamPrefix: 'my-stream',
        logGroup: props.backendLogGroup,
      }),
      environment:{
        // user:pass@endpoint:port/dbname
        "LANGFLOW_DATABASE_URL" : `mysql+pymysql://${ecs.Secret.fromSecretsManager(secretsDB, 'username')}:${ecs.Secret.fromSecretsManager(secretsDB, 'password')}@${ecs.Secret.fromSecretsManager(secretsDB, 'host')}:3306/${ecs.Secret.fromSecretsManager(secretsDB, 'dbname')}`,
        "LANGFLOW_AUTO_LOGIN" : "false",
        "LANGFLOW_SUPERUSER" : "admin",
        "LANGFLOW_SUPERUSER_PASSWORD" : "1234567"
      },
      portMappings: [
          {
              containerPort: containerPort,
              protocol: ecs.Protocol.TCP,
          },
      ],
      // Secretの設定
      secrets: {
        "dbname": ecs.Secret.fromSecretsManager(secretsDB, 'dbname'),
        "username": ecs.Secret.fromSecretsManager(secretsDB, 'username'),
        "host": ecs.Secret.fromSecretsManager(secretsDB, 'host'),
        "password": ecs.Secret.fromSecretsManager(secretsDB, 'password'),
      },
    });
    this.backendServiceName = 'langflow-backend-service'
    const backendService = new ecs.FargateService(this, 'BackEndService', {
      cluster: props.cluster,
      serviceName: this.backendServiceName,
      taskDefinition: backendTaskDefinition,
      enableExecuteCommand: true,
      securityGroups: [props.ecsBackSG],
      cloudMapOptions: {
        cloudMapNamespace: props.cloudmapNamespace,
        containerPort: containerPort,
        dnsRecordType: servicediscovery.DnsRecordType.A,
        dnsTtl: Duration.seconds(10),
        name: this.backendServiceName
      },
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
    });

    const ecsBackEndExecutionRole = iam.Role.fromRoleArn(
      this,
      "ecsBackEndExecutionRole",
      backendService.taskDefinition.executionRole!.roleArn,
      {}
    );
    ecsBackEndExecutionRole.attachInlinePolicy(new iam.Policy(this, 'SMGetPolicy', {
      statements: [new iam.PolicyStatement({
        actions: ['secretsmanager:GetSecretValue'],
        resources: [secretsDB.secretArn],
      })],
    }));

  }
}