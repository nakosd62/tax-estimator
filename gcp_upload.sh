#!/bin/bash

gcloud run deploy nyc-tax-estimator \
  --source . \
  --region us-central1 \
  --allow-unauthenticated