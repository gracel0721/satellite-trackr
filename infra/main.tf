locals {
  # Bucket names must be globally unique. Allow an explicit override, else
  # derive from the project id (matches the plan's documented URL).
  data_bucket = coalesce(var.data_bucket, "${var.project_id}-satellite-data")
  source_zip  = "${path.module}/function-source.zip"
}

# --- Enable the APIs the backend depends on -------------------------------
resource "google_project_service" "apis" {
  for_each = toset([
    "cloudfunctions.googleapis.com",   # Cloud Functions (2nd gen)
    "run.googleapis.com",              # gen2 functions run on Cloud Run
    "eventarc.googleapis.com",         # gen2 function triggers
    "cloudbuild.googleapis.com",       # builds the function image
    "cloudscheduler.googleapis.com",  # scheduled trigger
    "storage.googleapis.com",          # data + source buckets
    "artifactregistry.googleapis.com", # function build artifacts
  ])

  project                    = var.project_id
  service                    = each.value
  disable_dependent_services = false
}

# --- Service account: runs the function and invokes it from the scheduler --
resource "google_service_account" "runner" {
  account_id   = "satellite-refresh"
  display_name = "Satellite pipeline runner"
  description  = "Runs the refresh Cloud Function and is used by Cloud Scheduler to trigger it."
}

# Project metadata (number) so we can reference GCP-managed service accounts.
data "google_project" "project" {
  project_id = var.project_id
}

# Cloud Build needs to push the function's container image to Artifact Registry.
# The default Cloud Build service account lacks this role by default, which is
# the most common cause of gen2 function "missing permission" build failures.
resource "google_project_iam_member" "cloudbuild_ar_writer" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${data.google_project.project.number}@cloudbuild.gserviceaccount.com"
}

# Enabling Cloud Build via the API (rather than the console) does not always
# auto-grant the Cloud Build service account its builder role. Without it the
# gen2 function build fails with a "missing permission" error. Grant it
# explicitly to be safe.
resource "google_project_iam_member" "cloudbuild_builder" {
  project = var.project_id
  role    = "roles/cloudbuild.builds.builder"
  member  = "serviceAccount:${data.google_project.project.number}@cloudbuild.gserviceaccount.com"
}

# Gen2 function builds actually run as the Compute Engine default service
# account. It must be able to write build logs to Cloud Logging and push the
# image to Artifact Registry; grant both explicitly (the auto-granted Editor
# role is not always present, especially on projects created via the API).
locals {
  compute_sa = "${data.google_project.project.number}-compute@developer.gserviceaccount.com"
}

resource "google_project_iam_member" "compute_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${local.compute_sa}"
}

resource "google_project_iam_member" "compute_ar_writer" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${local.compute_sa}"
}

# The build reads the uploaded source from the GCF-managed source bucket
# (gcf-v2-sources-<project>-<region>); the Compute default SA needs object
# viewer on it (project-level grant covers the managed bucket).
resource "google_project_iam_member" "compute_storage_viewer" {
  project = var.project_id
  role    = "roles/storage.objectViewer"
  member  = "serviceAccount:${local.compute_sa}"
}

# --- Function source archive (repo root minus non-deploy dirs) -----------
data "archive_file" "function_source" {
  type        = "zip"
  source_dir  = "${path.module}/.."
  output_path = local.source_zip
  excludes = [
    ".venv",
    ".git",
    "data",
    "public",
    "plans",
    "infra",
    ".github",
    "api",
    "README.md",
    # Vercel-only entrypoint/deps; GCP builds from requirements.txt, not pyproject.
    "pyproject.toml",
  ]
}

# A small private bucket just to stage the source zip for each build.
resource "google_storage_bucket" "source" {
  name                     = "${local.data_bucket}-src"
  location                 = var.region
  force_destroy            = true
  uniform_bucket_level_access = true
  public_access_prevention = "enforced"
}

resource "google_storage_bucket_object" "source" {
  name   = "function-${data.archive_file.function_source.output_md5}.zip"
  bucket = google_storage_bucket.source.name
  source = local.source_zip

  # Re-deploy whenever the source changes.
  depends_on = [data.archive_file.function_source]
}

# --- Public data bucket: positions.json the frontend fetches -------------
resource "google_storage_bucket" "data" {
  name                     = local.data_bucket
  location                 = var.region
  force_destroy            = true
  uniform_bucket_level_access = true
  public_access_prevention = "inherited"

  # The browser fetches positions.json directly from this bucket (different
  # origin than the app), so it must serve CORS headers for cross-origin GETs.
  cors {
    origin          = ["*"]
    method          = ["GET", "HEAD"]
    response_header = ["Content-Type", "Access-Control-Allow-Origin"]
    max_age_seconds = 3600
  }
}

# Anyone can read positions.json — the frontend fetches it straight from GCS.
resource "google_storage_bucket_iam_member" "public_read" {
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectViewer"
  member = "allUsers"
}

# The function's service account writes the object.
resource "google_storage_bucket_iam_member" "fn_write" {
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.runner.email}"
}

# --- Cloud Function (2nd gen, HTTP) --------------------------------------
resource "google_cloudfunctions2_function" "refresh" {
  name        = var.function_name
  location    = var.region
  description = "Fetch Starlink TLEs, propagate positions, flag close approaches, publish positions.json."

  build_config {
    runtime     = "python311"
    entry_point = "refresh"
    source {
      storage_source {
        bucket = google_storage_bucket.source.name
        object = google_storage_bucket_object.source.name
      }
    }
  }

  service_config {
    service_account_email = google_service_account.runner.email
    available_memory      = "${var.memory_mb}Mi"
    timeout_seconds       = var.timeout_seconds
    max_instance_count    = 1
    # Allow manual curl tests from outside; the scheduler still authenticates.
    ingress_settings      = "ALLOW_ALL"
    environment_variables = {
      DATA_BUCKET = local.data_bucket
      DATA_DIR    = "/tmp/data"
      SAT_GROUPS  = var.sat_groups
      # Analysis runs at full 1-min resolution; only the committed payload is
      # decimated to 5-min to keep the public JSON small for the browser.
      TIME_WINDOW_HRS = "24"
      STEP_MIN        = "1"
      OUTPUT_STEP_MIN = "5"
      # /tmp is empty on every cold start, so never pretend a cache is fresh.
      CACHE_TTL_HRS = "0"
    }
  }

  depends_on = [
    google_project_service.apis,
    google_project_iam_member.cloudbuild_ar_writer,
    google_project_iam_member.cloudbuild_builder,
    google_project_iam_member.compute_log_writer,
    google_project_iam_member.compute_ar_writer,
    google_project_iam_member.compute_storage_viewer,
  ]
}

# Let the runner service account invoke the function (used by the scheduler).
resource "google_cloudfunctions2_function_iam_member" "invoker" {
  cloud_function = google_cloudfunctions2_function.refresh.name
  location       = var.region
  role           = "roles/cloudfunctions.invoker"
  member         = "serviceAccount:${google_service_account.runner.email}"
}

# Gen2 functions run on Cloud Run, so the scheduler's OIDC-authenticated request
# is authorized against the *underlying Cloud Run service* IAM (run.routes.invoke).
# The cloudfunctions.invoker grant above is meant to propagate to it but does
# not reliably, so grant roles/run.invoker on the Cloud Run service explicitly.
# Without this the scheduler gets a 403 "lacks run.routes.invoke" on every run.
resource "google_cloud_run_service_iam_member" "invoker" {
  location = var.region
  service  = google_cloudfunctions2_function.refresh.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.runner.email}"

  depends_on = [google_cloudfunctions2_function.refresh]
}

# --- Cloud Scheduler: trigger the function every 12 hours ----------------
resource "google_cloud_scheduler_job" "refresh" {
  name      = var.function_name
  schedule  = var.schedule
  time_zone = "UTC"
  region    = var.region

  http_target {
    uri         = google_cloudfunctions2_function.refresh.url
    http_method = "POST"
    oidc_token {
      service_account_email = google_service_account.runner.email
      audience               = google_cloudfunctions2_function.refresh.url
    }
  }

  depends_on = [
    google_cloudfunctions2_function_iam_member.invoker,
    google_cloud_run_service_iam_member.invoker,
    google_project_service.apis,
  ]
}