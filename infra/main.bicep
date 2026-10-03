// TB X-ray: all Azure resources for a single environment.
//
// Two-phase deployment (the CD pipeline does both on every run, idempotently):
//   1. deployApps=false  -> registry, storage, identities, logs, Container Apps environment
//   2. push images to the registry
//   3. deployApps=true   -> backend (private) + frontend (public) container apps with the new image tags
//
// Model files live in the storage account (container "models", folder "tb-xray/"); CD downloads them and
// bakes them into the backend image. No passwords or keys anywhere: apps pull images with managed identities,
// storage has shared-key access disabled, and the backend has internal-only ingress behind the frontend proxy.

targetScope = 'resourceGroup'

@description('Azure region')
param location string = resourceGroup().location

@description('Short lowercase alphanumeric prefix for resource names')
@minLength(2)
@maxLength(8)
param namePrefix string = 'tbx'

@description('false = shared infrastructure only (first phase / bootstrap)')
param deployApps bool = false

@description('Full backend image reference, e.g. <acr>.azurecr.io/tb-xray-api:<git-sha>')
param apiImage string = ''

@description('Full frontend image reference')
param webImage string = ''

@minValue(0)
param apiMinReplicas int = 0

@minValue(1)
param apiMaxReplicas int = 2

param tags object = {
  app: 'tb-xray'
  managedBy: 'bicep'
}

var suffix = uniqueString(resourceGroup().id)
var modelsContainer = 'models'

// Built-in role definition IDs
var roleAcrPull = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

// ---------------------------------------------------------------- observability
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${namePrefix}-logs-${suffix}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

// ---------------------------------------------------------------- container registry
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: '${namePrefix}acr${suffix}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false
  }
}

// ---------------------------------------------------------------- model storage
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: '${namePrefix}st${suffix}'
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false // Entra ID (managed identity / az login) only
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    isVersioningEnabled: true // accidental overwrites of model files can be recovered
    deleteRetentionPolicy: { enabled: true, days: 14 }
    containerDeleteRetentionPolicy: { enabled: true, days: 14 }
  }
}

resource modelsBlobContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: modelsContainer
  properties: { publicAccess: 'None' }
}

// ---------------------------------------------------------------- identities + least-privilege roles
resource apiIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${namePrefix}-id-api'
  location: location
  tags: tags
}

resource webIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${namePrefix}-id-web'
  location: location
  tags: tags
}

resource apiAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, apiIdentity.id, roleAcrPull)
  scope: acr
  properties: {
    principalId: apiIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleAcrPull)
  }
}

resource webAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, webIdentity.id, roleAcrPull)
  scope: acr
  properties: {
    principalId: webIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleAcrPull)
  }
}

// ---------------------------------------------------------------- Container Apps environment
resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${namePrefix}-env'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

// ---------------------------------------------------------------- backend API (private)
resource api 'Microsoft.App/containerApps@2024-03-01' = if (deployApps) {
  name: '${namePrefix}-api'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${apiIdentity.id}': {} }
  }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: false // reachable only inside the environment (through the frontend proxy)
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        { server: acr.properties.loginServer, identity: apiIdentity.id }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: apiImage
          resources: { cpu: json('2.0'), memory: '4Gi' }
          probes: [
            {
              type: 'Startup' // the API answers only after all models are loaded
              httpGet: { path: '/api/health', port: 8000 }
              initialDelaySeconds: 10
              periodSeconds: 10
              failureThreshold: 30
            }
            {
              type: 'Liveness'
              httpGet: { path: '/api/health', port: 8000 }
              periodSeconds: 30
              failureThreshold: 3
            }
          ]
        }
      ]
      scale: {
        minReplicas: apiMinReplicas
        maxReplicas: apiMaxReplicas
        rules: [
          { name: 'http', http: { metadata: { concurrentRequests: '4' } } }
        ]
      }
    }
  }
  dependsOn: [apiAcrPull]
}

// ---------------------------------------------------------------- frontend (public)
resource web 'Microsoft.App/containerApps@2024-03-01' = if (deployApps) {
  name: '${namePrefix}-web'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${webIdentity.id}': {} }
  }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        { server: acr.properties.loginServer, identity: webIdentity.id }
      ]
    }
    template: {
      containers: [
        {
          name: 'web'
          image: webImage
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
          env: [
            { name: 'BACKEND_URL', value: 'https://${api!.properties.configuration.ingress.fqdn}' }
            { name: 'BACKEND_HOST', value: api!.properties.configuration.ingress.fqdn }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/healthz', port: 8080 }
              periodSeconds: 30
            }
          ]
        }
      ]
      scale: { minReplicas: 0, maxReplicas: 2 }
    }
  }
  dependsOn: [webAcrPull]
}

// ---------------------------------------------------------------- outputs
output acrName string = acr.name
output acrLoginServer string = acr.properties.loginServer
output storageAccountName string = storage.name
output storageBlobEndpoint string = storage.properties.primaryEndpoints.blob
output modelsContainer string = modelsContainer
output apiAppName string = '${namePrefix}-api'
output webAppName string = '${namePrefix}-web'
output webUrl string = deployApps ? 'https://${web!.properties.configuration.ingress.fqdn}' : ''
