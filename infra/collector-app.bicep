// ============================================================================
// Claude Code Observability — Azure M3: OTel Collector Container App
// ============================================================================
//
// Deploys the collector Container App into the environment created by M1
// (main.bicep). Depends on M1 outputs for identity, endpoints, and CAE.
//
// Network model:
//   - Ingress is public HTTPS (4318 OTLP/HTTP), gated by ipSecurityRestrictions
//     to the CIDRs in `allowedClientCidrs`. Random internet traffic is rejected
//     at the platform edge before the collector process sees it.
//   - Egress (collector → DCE) uses Entra-auth'd bearer tokens from the
//     user-assigned Managed Identity, so no shared secret is in play.
//
// Config delivery:
//   - The collector-config.azure.yaml at the repo root is read at Bicep
//     compile time via loadTextContent() and passed as an ACA secret. The
//     secret is then mounted as a volume into /etc/otel so the collector
//     binary can read it with --config=/etc/otel/collector-config.yaml.
//     Single deploy, no file-share chicken-and-egg.
//
// Deploy:
//   az deployment group create \
//     --resource-group <rg> \
//     --template-file infra/collector-app.bicep \
//     --parameters \
//         baseName=agent-otel \
//         allowedClients='[{"cidr":"x.x.x.x/32","description":"John Stanford"}]' \
//         metricsRemoteWriteUrl=<from M1 outputs> \
//         appInsightsConnectionString=<from M1 outputs> \
//         revisionSuffix=v2  # bump to force secret-volume refresh
// ============================================================================

targetScope = 'resourceGroup'

// ------------------------ Parameters ---------------------------------------

@description('Base name used in M1. All M1-created resources are looked up by the same prefix.')
param baseName string = 'agent-otel'

@description('Azure region.')
param location string = resourceGroup().location

@description('Allowlisted clients for OTLP ingress. Each item: { cidr: "x.x.x.x/32", description: "Owner name" }. Description appears in the Azure portal next to each rule.')
param allowedClients array

@description('Optional revision suffix. Increment to force a new revision when only secret values changed (e.g. after editing collector-config.azure.yaml). Leave empty for normal redeploys.')
param revisionSuffix string = ''

@description('Full Prometheus remote-write URL from M1 output `metricsRemoteWriteUrl`.')
param metricsRemoteWriteUrl string

@description('App Insights connection string from M1 output `appInsightsConnectionString`. Treated as a credential — passed via Container App secrets, not plaintext env.')
@secure()
param appInsightsConnectionString string

@description('Container image tag for otel-collector-contrib. Must be >= 0.124.0 (the azureauthextension was added then) but skip 0.116.x (dynamically-linked binary regression that breaks the FROM-scratch image). 0.140.0 verified statically linked + has azure_auth.')
param collectorImageTag string = '0.140.0'

// ------------------------ Derived names ------------------------------------

var identityName               = '${baseName}-collector-mi'
var containerAppsEnvName       = '${baseName}-cae'
var collectorAppName           = '${baseName}-collector'

// ------------------------ Existing M1 resources ----------------------------

resource collectorIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: identityName
}

resource cae 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: containerAppsEnvName
}

// ------------------------ Collector Container App --------------------------

// Read the collector config from disk at deploy time. Path is relative to
// this Bicep file.
var collectorConfigYaml = loadTextContent('../collector-config.azure.yaml')

resource collectorApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: collectorAppName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${collectorIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: cae.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 4318
        transport: 'http'
        allowInsecure: false
        // Platform-level IP allowlist. Requests from non-listed source IPs
        // receive an HTTP 403 without ever reaching the collector.
        ipSecurityRestrictions: [for (entry, i) in allowedClients: {
          name: 'allow-${replace(toLower(entry.description), ' ', '-')}'
          action: 'Allow'
          ipAddressRange: entry.cidr
          description: entry.description
        }]
      }
      secrets: [
        {
          // The entire collector config is stored as a secret value. It becomes
          // a file inside the container via the volumes[].secrets mapping below.
          // The config itself isn't secret (it's committed in the repo) — the
          // ACA "secret" slot is just how non-env-var string values get mounted
          // as files. Hence the linter suppression below.
          name: 'collector-config'
          #disable-next-line use-secure-value-for-secure-inputs
          value: collectorConfigYaml
        }
        {
          // App Insights connection string. Genuinely sensitive — embeds the
          // instrumentation key that authenticates writes to the AI resource.
          name: 'app-insights-connection-string'
          value: appInsightsConnectionString
        }
      ]
    }
    template: {
      revisionSuffix: revisionSuffix
      containers: [
        {
          name: 'otel-collector'
          image: 'otel/opentelemetry-collector-contrib:${collectorImageTag}'
          // The contrib image's entrypoint is the binary; we pass --config
          // pointing at the mounted secret file.
          args: [
            '--config=/etc/otel/collector-config.yaml'
          ]
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            {
              // Picked up by the azureauth extension in collector-config.azure.yaml
              name: 'AZURE_CLIENT_ID'
              value: collectorIdentity.properties.clientId
            }
            {
              name: 'AZURE_MONITOR_METRICS_ENDPOINT'
              value: metricsRemoteWriteUrl
            }
            {
              // Resolved from ACA secret at runtime — connection string never
              // appears in plaintext on the container template.
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              secretRef: 'app-insights-connection-string'
            }
          ]
          volumeMounts: [
            {
              volumeName: 'config'
              mountPath: '/etc/otel'
            }
          ]
          probes: [
            {
              type: 'Readiness'
              httpGet: {
                path: '/'
                port: 13133  // health_check extension in the collector config
              }
              periodSeconds: 10
              failureThreshold: 3
            }
            {
              type: 'Liveness'
              httpGet: {
                path: '/'
                port: 13133
              }
              initialDelaySeconds: 30
              periodSeconds: 30
              failureThreshold: 3
            }
          ]
        }
      ]
      volumes: [
        {
          // 'Secret' storageType projects each listed secret as a file at
          // <mountPath>/<path>. collector-config secret → /etc/otel/collector-config.yaml.
          name: 'config'
          storageType: 'Secret'
          secrets: [
            {
              secretRef: 'collector-config'
              path: 'collector-config.yaml'
            }
          ]
        }
      ]
      scale: {
        // Pin to 1 replica. Scaling the collector introduces duplicate-emission
        // concerns with stateful batching, and at single-developer volume
        // one replica is more than enough.
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

// ------------------------ Outputs ------------------------------------------

@description('FQDN of the collector ingress. Send OTLP/HTTP here.')
output collectorFqdn string = collectorApp.properties.configuration.ingress.fqdn

@description('Full OTLP endpoint for Claude Code clients. Set OTEL_EXPORTER_OTLP_ENDPOINT to this value.')
output otlpHttpEndpoint string = 'https://${collectorApp.properties.configuration.ingress.fqdn}'

@description('Client setup command summary.')
output clientSetupHint string = 'export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf; export OTEL_EXPORTER_OTLP_ENDPOINT=https://${collectorApp.properties.configuration.ingress.fqdn}; export CLAUDE_CODE_ENABLE_TELEMETRY=1; export OTEL_METRICS_EXPORTER=otlp; export OTEL_LOGS_EXPORTER=otlp'
