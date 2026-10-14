#!/bin/bash

gcloud run deploy tax-estimator \
  --source . \
  --region us-east1 \
  --allow-unauthenticated