# Renovate Central

A central repository for managing and syncing Renovate configuration files across multiple GitHub repositories. By storing all configurations in one place and leveraging GitHub Actions-based automation, this tool simplifies the maintenance and synchronization of Renovate settings.

## How It Works

- **Step 1**: Store your renovate configuration files in the `renovate/` directory of this repository.
  
- **Step 2**: Configure your repositories by editing the `config.yaml` file.

- **Step 3**: Run the `sync-renovate-configs.yaml` Github Actions Workflow.

## How To Configure?

Edit the `config.yaml` file to specify:
```yaml
- renovate-config: "renovate/default-renovate-distribution.json"
  sync-repositories:
    - name: "red-hat-data-services/trustyai-explainability"
    - name: "red-hat-data-services/argo-workflows"
      targetFilePath: "renovate.json"

- renovate-config: "renovate/custom-renovate-distribution.json"
  sync-repositories:
    - name: "red-hat-data-services/data-science-pipelines-operator"
    - name: "red-hat-data-services/trustyai-service-operator"
```

- `renovate-config`: Specifies which renovate configuration files in the `renovate/` directory to use.
- `sync-repositories`: A list of repositories that will have their renovate configuration files synced with the specified `renovate-config`.
- `targetFilePath` (optional): Allows specifying a custom path for the renovate config file in a given repository. If not specified, the default path is `.github/renovate.json`.
