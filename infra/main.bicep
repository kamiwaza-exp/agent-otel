// ============================================================================
// Claude Code Observability — Azure M1 infrastructure
// ============================================================================
//
// Deploys the managed backends for the Azure deployment described in
// docs/azure-deployment-design.md, milestone M1.
//
// After this deploys, data still isn't flowing — M2 (collector config) and
// M3 (Container App deploy) come next. This file provisions:
//
//   - User-assigned Managed Identity (for the collector to auth to AMW)
//   - Azure Monitor Workspace + DCE + metrics DCR (Managed Prometheus)
//   - Log Analytics Workspace (the actual store for both metrics-adjacent
//     workloads and logs)
//   - Application Insights (workspace-based) → ingestion door for OTLP logs;
//     stores into the LA workspace; sampling disabled
//   - Container Apps Environment (the app itself is deployed in M3)
//   - Azure Managed Grafana (Essential SKU)
//   - Role assignments wiring collector MI → metrics ingestion and
//     Grafana → read rights on AMW + LAW
//
// Why App Insights instead of a custom LA table:
//   The OTel Collector's otlphttp exporter sends OTLP wire format. Azure
//   Monitor's Log Ingestion API requires a custom-stream JSON shape that
//   does not match OTLP. The clean GA path for OTLP-shaped logs is the
//   `azuremonitor` exporter → Application Insights. To preserve the
//   "no-sampling" intent, the AI resource is created in workspace-based
//   mode (data lands in the LA workspace as App* tables) and the
//   adaptive sampling cap is set to 100% (no drops).
//
// Scope: Resource Group. Create the RG with `az group create` before deploying.
// Deploy with:
//   az deployment group create \
//     --resource-group <rg-name> \
//     --template-file infra/main.bicep \
//     --parameters baseName=agent-otel location=centralus
// ============================================================================

targetScope = 'resourceGroup'

// ------------------------ Parameters ---------------------------------------

@description('Base name for all resources. Used as prefix.')
param baseName string = 'agent-otel'

@description('Azure region. Must support Azure Monitor Workspace and Managed Grafana.')
param location string = resourceGroup().location

@description('Log Analytics retention in days (30-730).')
@minValue(30)
@maxValue(730)
param logRetentionDays int = 30

@description('Daily ingestion cap for Log Analytics in GB. Protects against runaway event volume.')
param logDailyCapGb int = 1

@description('Entra object IDs of users/groups to grant Grafana Admin.')
param grafanaAdminPrincipalIds array = []

// ------------------------ Derived names ------------------------------------

var identityName       = '${baseName}-collector-mi'
var amwName            = '${baseName}-amw'          // Azure Monitor Workspace
var lawName            = '${baseName}-law'          // Log Analytics Workspace
var dceName            = '${baseName}-dce'          // Data Collection Endpoint
var dcrMetricsName     = '${baseName}-dcr-metrics'
var caeName            = '${baseName}-cae'          // Container Apps Environment
var grafanaName        = '${baseName}-grafana'
var appInsightsName    = '${baseName}-ai'           // Application Insights

// Built-in Azure role IDs (stable across tenants; safe to hardcode).
var role_MonitoringMetricsPublisher = '3913510d-42f4-4e42-8a64-420c390055eb'
var role_MonitoringReader           = '43d0d8ad-25c7-4714-9337-8ba259a9fe05'
var role_LogAnalyticsReader         = '73c42c96-874c-492b-b04d-ab87d138a893'

// ------------------------ Managed Identity ---------------------------------

resource collectorIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
}

// ------------------------ Metrics: Azure Monitor Workspace -----------------

resource amw 'Microsoft.Monitor/accounts@2023-04-03' = {
  name: amwName
  location: location
  properties: {}
}

// ------------------------ Logs: Log Analytics Workspace --------------------

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: lawName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: logRetentionDays
    workspaceCapping: {
      dailyQuotaGb: logDailyCapGb
    }
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
  }
}

// ------------------------ Application Insights ----------------------------
// Workspace-based: data physically lands in the linked LA workspace as
// App* tables (AppTraces, AppEvents, AppDependencies, AppMetrics). The AI
// resource is just the ingestion door — there's no separate AI store.
//
// SamplingPercentage: 100 disables the AI backend's adaptive sampling, which
// would otherwise drop a fraction of events under load. The OTel Collector's
// `azuremonitor` exporter does not apply its own sampling unless configured.

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    Flow_Type: 'Bluefield'              // workspace-based mode
    Request_Source: 'rest'
    WorkspaceResourceId: law.id
    SamplingPercentage: 100             // no ingestion sampling
    DisableIpMasking: false
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
    IngestionMode: 'LogAnalytics'
  }
}

// ------------------------ Data Collection Endpoint -------------------------
// Serves Prometheus remote-write metrics for the Managed Prometheus path.
// Logs path uses App Insights' own ingestion endpoint, separate from this DCE.

resource dce 'Microsoft.Insights/dataCollectionEndpoints@2023-03-11' = {
  name: dceName
  location: location
  properties: {
    networkAcls: {
      publicNetworkAccess: 'Enabled'
    }
  }
}

// ------------------------ DCR: Metrics (Prometheus) ------------------------
// kind: 'Direct' — direct ingestion via remote-write API. (Linux/Windows
// kinds are for agent-collected data sources and require non-empty
// dataSources, which we don't have here.)

resource dcrMetrics 'Microsoft.Insights/dataCollectionRules@2023-03-11' = {
  name: dcrMetricsName
  location: location
  kind: 'Direct'
  properties: {
    dataCollectionEndpointId: dce.id
    destinations: {
      monitoringAccounts: [
        {
          accountResourceId: amw.id
          name: 'amw-dest'
        }
      ]
    }
    dataFlows: [
      {
        streams: [ 'Microsoft-PrometheusMetrics' ]
        destinations: [ 'amw-dest' ]
      }
    ]
  }
}

// ------------------------ Role assignments: Collector Identity -------------

// Metrics DCR: Monitoring Metrics Publisher lets the identity POST to the DCE
// for remote-write ingestion of Prometheus metrics.
resource raMetricsPublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(dcrMetrics.id, collectorIdentity.id, role_MonitoringMetricsPublisher)
  scope: dcrMetrics
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role_MonitoringMetricsPublisher)
    principalId: collectorIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Note: there is no role assignment for the App Insights logs path.
// The `azuremonitor` exporter authenticates with the AI ingestion endpoint
// using the connection string (which embeds an instrumentation key), not
// Entra ID. The connection string is passed to the Container App as a
// secret in the M3 template.

// ------------------------ Container Apps Environment -----------------------
// The Container App itself is deployed in M3 (separate template). This
// environment is the parent.

resource cae 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: caeName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: law.properties.customerId
        sharedKey: law.listKeys().primarySharedKey
      }
    }
    // Consumption-only workload profile keeps cost minimal.
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

// ------------------------ Azure Managed Grafana ----------------------------

// Standard is the only SKU Azure Managed Grafana ships now (Essential was
// deprecated). Standard is billed per active user (~$8/user/mo) and unlocks
// alerting we don't currently use.
resource grafana 'Microsoft.Dashboard/grafana@2023-09-01' = {
  name: grafanaName
  location: location
  sku: {
    name: 'Standard'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    publicNetworkAccess: 'Enabled'
    grafanaIntegrations: {
      azureMonitorWorkspaceIntegrations: [
        {
          azureMonitorWorkspaceResourceId: amw.id
        }
      ]
    }
  }
}

// Grafana needs read access to query Managed Prometheus.
resource raGrafanaAmwReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(amw.id, grafana.id, role_MonitoringReader)
  scope: amw
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role_MonitoringReader)
    principalId: grafana.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Grafana needs read access to query the Log Analytics workspace
// (where AppTraces/AppEvents land via workspace-based AI).
resource raGrafanaLawReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(law.id, grafana.id, role_LogAnalyticsReader)
  scope: law
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role_LogAnalyticsReader)
    principalId: grafana.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Grafana needs read access on the App Insights component itself for
// the "Azure Traces" query type used by the session-trace dashboard.
// IMPORTANT: Monitoring Reader is NOT sufficient — its */read wildcard
// does not cover the data-plane action microsoft.insights/transactions/read
// (verified empirically). Use Application Insights Component Contributor,
// which explicitly lists transactions/read in its actions array.
var role_AppInsightsComponentContributor = 'ae349356-3a1b-4a5e-921d-050484c6347e'
resource raGrafanaAiContrib 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(appInsights.id, grafana.id, role_AppInsightsComponentContributor)
  scope: appInsights
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role_AppInsightsComponentContributor)
    principalId: grafana.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Grant each listed admin principal the Grafana Admin role (data-plane).
resource raGrafanaAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (principalId, i) in grafanaAdminPrincipalIds: {
  name: guid(grafana.id, principalId, 'grafana-admin')
  scope: grafana
  properties: {
    // Grafana Admin role ID
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '22926164-76b3-42b3-bc55-97df8dab3e41')
    principalId: principalId
    principalType: 'User'
  }
}]

// ------------------------ Outputs ------------------------------------------
// Consumed by the M2/M3 collector + Container App deploys.

@description('Client ID of the user-assigned Managed Identity. Set as AZURE_CLIENT_ID on the Container App.')
output collectorIdentityClientId string = collectorIdentity.properties.clientId

@description('Resource ID of the user-assigned Managed Identity. Attach to the Container App.')
output collectorIdentityResourceId string = collectorIdentity.id

@description('DCE metrics ingestion endpoint. Used to compose metricsRemoteWriteUrl.')
output dceMetricsIngestionEndpoint string = dce.properties.metricsIngestion.endpoint

@description('Immutable ID of the metrics DCR.')
output dcrMetricsImmutableId string = dcrMetrics.properties.immutableId

@description('Full Prometheus remote-write URL for AZURE_MONITOR_METRICS_ENDPOINT.')
output metricsRemoteWriteUrl string = '${dce.properties.metricsIngestion.endpoint}/dataCollectionRules/${dcrMetrics.properties.immutableId}/streams/Microsoft-PrometheusMetrics/api/v1/write'

@description('App Insights connection string for the azuremonitor exporter. Set as APPLICATIONINSIGHTS_CONNECTION_STRING on the Container App. Treat as a credential (it embeds an instrumentation key).')
output appInsightsConnectionString string = appInsights.properties.ConnectionString

@description('App Insights resource ID. For Grafana data source config if querying via AI directly rather than the linked LA workspace.')
output appInsightsResourceId string = appInsights.id

@description('Log Analytics Workspace resource ID. Grafana queries AppTraces/AppEvents here.')
output logAnalyticsWorkspaceId string = law.id

@description('Azure Monitor Workspace resource ID (for Grafana data source config).')
output azureMonitorWorkspaceId string = amw.id

@description('Grafana endpoint URL.')
output grafanaEndpoint string = grafana.properties.endpoint

@description('Container Apps Environment resource ID. Used by M3 to deploy the collector.')
output containerAppsEnvironmentId string = cae.id
