output "function_url" {
  description = "HTTP URL of the Cloud Function (POST to trigger a manual refresh)."
  value       = google_cloudfunctions2_function.refresh.url
}

output "data_bucket_url" {
  description = "Base public URL of the data bucket."
  value       = "https://storage.googleapis.com/${google_storage_bucket.data.name}"
}

output "positions_json_url" {
  description = "The public positions.json URL the frontend should fetch."
  value       = "https://storage.googleapis.com/${google_storage_bucket.data.name}/positions.json"
}

output "service_account_email" {
  value = google_service_account.runner.email
}