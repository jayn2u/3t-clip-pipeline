# Local state to start. Once more than one operator applies, switch to the
# commented S3-compatible backend below, pointed at MinIO itself (the cluster
# already runs an S3-compatible store, so no new infra is needed to host state).
#
# terraform {
#   backend "s3" {
#     bucket                      = "lab-terraform-state"
#     key                         = "3t-clip-pipeline/terraform.tfstate"
#     region                      = "us-east-1"          # unused by MinIO, required by the provider
#     endpoints                   = { s3 = "https://minio.internal:9000" }
#     skip_credentials_validation = true
#     skip_region_validation      = true
#     skip_requesting_account_id  = true
#     use_path_style               = true
#     use_lockfile                 = true                 # S3 native locking (Terraform >= 1.10)
#   }
# }
