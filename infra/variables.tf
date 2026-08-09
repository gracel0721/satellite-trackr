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
  default     = "starlink,oneweb,iridium-NEXT,gnss,fengyun-1c-debris,iridium-33-debris,cosmos-2251-debris"
  description = "CelesTrak group(s) to fetch, comma-separated. Default covers Starlink + OneWeb + Iridium-NEXT + GNSS + the three historical fragmentation debris populations."
}

variable "n_max" {
  type        = number
  default     = 20000
  description = "Optional cap on total satellites processed. The multi-regime default set totals ~11-12k sats (Starlink alone exceeds 8000), so the cap must sit above that or the merged-list prefix (Starlink first) crowds out every other group. 20000 keeps all 7 groups through the cap as a memory guard. Empty string disables."
}

variable "memory_mb" {
  type        = number
  default     = 8192
  description = "Cloud Function memory. The multi-regime default (Starlink + OneWeb + Iridium-NEXT + GNSS + 3 debris groups, capped at N_MAX) is held in memory as per-satellite ecef/velocity lists for propagation + analysis; peak is ~5GiB, so 8GiB gives headroom. Still within the free-tier GiB-second budget at 2 runs/day (8GiB x 540s x 2/day ~ 260k GiB-s/mo < 360k free)."
}

variable "timeout_seconds" {
  type        = number
  default     = 540
  description = "Cloud Function timeout. The capped multi-regime set + 24h propagation comfortably fits under 9 minutes."
}