# Satellite Backend — Setup Guide (from scratch)

This deploys a small serverless backend that fetches Starlink TLE data, propagates
positions, flags close approaches, and writes the result to a public JSON file
your Cesium frontend can fetch directly. Runs every 12 hours, entirely on GCP's
Always Free tier.

## 1. Install the tools

- **gcloud CLI**: https://cloud.google.com/sdk/docs/install
- **Terraform**: https://developer.hashicorp.com/terraform/install

Verify:
```bash
gcloud --version
terraform --version
```

## 2. Create a GCP project

```bash
gcloud auth login
gcloud projects create YOUR-PROJECT-ID --name="Satellite Tracker"
gcloud config set project YOUR-PROJECT-ID
```

**Important:** GCP requires a billing account linked even for free-tier usage
(you won't be charged as long as you stay within free limits). Link one via
the console: https://console.cloud.google.com/billing — then attach it:

```bash
gcloud billing accounts list
gcloud billing projects link YOUR-PROJECT-ID --billing-account=YOUR-BILLING-ACCOUNT-ID
```

## 3. Authenticate Terraform

```bash
gcloud auth application-default login
```

## 4. Deploy

From this directory:

```bash
terraform init
terraform apply -var="project_id=YOUR-PROJECT-ID"
```

Type `yes` when prompted. First apply will take a few minutes (API enablement +
function build).

## 5. Test it manually

Trigger the function once by hand instead of waiting 3 hours:

```bash
curl -X POST $(terraform output -raw function_url) \
  -H "Authorization: bearer $(gcloud auth print-identity-token)"
```

Then check the data landed:

```bash
curl $(terraform output -raw data_bucket_url)/positions.json
```

## 6. Point your frontend at it

In your Cesium app, replace the client-side fetch/propagation logic with a
single fetch of:

```
https://storage.googleapis.com/YOUR-PROJECT-ID-satellite-data/positions.json
```

This JSON already contains propagated positions and flagged close approaches —
your browser just renders it, no more heavy computation client-side.

## Free tier notes

- Cloud Function: runs 2x/day, ~300s each → nowhere close to the 2M invocations
  / 400,000 GB-seconds free monthly allowance
- Cloud Scheduler: 1 job used, 3 are free
- Cloud Storage: a few MB of JSON, well under the 5GB free allowance (must stay
  in a US region for the storage free tier to apply — `us-central1` qualifies)

## Teardown

To avoid any charges if you stop using it:

```bash
terraform destroy -var="project_id=YOUR-PROJECT-ID"
```
