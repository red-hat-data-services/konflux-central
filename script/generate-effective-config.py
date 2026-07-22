import yaml
import argparse

# Function to read the YAML configuration from a file
def read_yaml(file_path):
    with open(file_path, 'r') as file:
        return yaml.safe_load(file)

# Function to write the YAML configuration to a file
def write_yaml(data, file_path):
    with open(file_path, 'w') as file:
        yaml.dump(data, file, default_flow_style=False)

# Function to parse command-line arguments
def parse_arguments():
    parser = argparse.ArgumentParser()

    # Add arguments with default values
    parser.add_argument(
        '--target-file-path', 
        type=str, 
        default='.github/renovate.json',
        required=False,
        help="Default path for target file (default: '.github/renovate.json')"
    )
    parser.add_argument(
        '--config-file', 
        type=str, 
        default='config.yaml',
        required=False,
        help="Path to the input YAML configuration file (default: 'config.yaml')"
    )
    parser.add_argument(
        '--output', 
        type=str, 
        default='effective-config.yaml',
        required=False,
        help="Path for the output YAML configuration file (default: 'effective-config.yaml')"
    )

    return parser.parse_args()

# Main function to process the input and generate the effective config
def main():
    # Parse arguments
    args = parse_arguments()

    # Read the config data from the input file
    input_file_path = args.config_file
    output_file_path = args.output
    target_file_path = args.target_file_path

    # Read the YAML data from the config file
    config_data = read_yaml(input_file_path)

    # Prepare the list for the effective config
    effective_config = []

    # Iterate through the provided config and create the new effective config
    for entry in config_data:
        renovate_config = entry["renovate-config"]
        for repo in entry["sync-repositories"]:
            repo_name = repo["name"]
            # If targetFilePath is not provided, set the default value from arguments
            final_target_file_path = repo.get("targetFilePath", target_file_path)
            effective_config.append({
                "renovate-config": renovate_config,
                "repo": repo_name,
                "targetFilePath": final_target_file_path
            })

    # Write the effective config to the output YAML file
    write_yaml(effective_config, output_file_path)

    print(f"Effective config written to {output_file_path}")

if __name__ == "__main__":
    main()
