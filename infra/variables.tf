variable "project_id" {
  type        = string
  description = "GCP project id. Must already exist with a billing account linked (created via gcloud, not Terraform)."
}

variable "region" {
  type        = string
  default     = "us-central1"
  description = "Region for the function, bucket, and scheduler. Must be a US region for the storage free tier."
}

variable "function_name" {
  type    = string
  default = "satellite-refresh"
}

variable "data_bucket" {
  type        = string
  default     = ""
  description = "GCS bucket name for the public positions.json. Must be globally unique. Defaults to <project_id>-satellite-data."
}

variable "schedule" {
  type        = string
  default     = "0 */12 * * *"
  description = "Cloud Scheduler cron expression (UTC). Default = every 12 hours (2x/day)."
}

variable "sat_groups" {
  type        = string
  default     = "starlink"
  description = "CelesTrak group(s) to fetch, comma-separated."
}

variable "memory_mb" {
  type        = number
  default     = 8192
  description = "Cloud Function memory. The full Starlink group (~10.8k sats) is held in memory as per-satellite ecef/velocity lists for propagation + analysis; peak is ~5GiB, so 8GiB gives headroom. Still within the free-tier GiB-second budget at 2 runs/day (8GiB x 540s x 2/day ~ 260k GiB-s/mo < 360k free)."
}

variable "timeout_seconds" {
  type        = number
  default     = 540
  description = "Cloud Function timeout. The full Starlink set + 24h propagation comfortably fits under 9 minutes."
}