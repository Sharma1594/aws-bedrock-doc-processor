# 🤖 Intelligent Document Processing with Amazon Bedrock

An event-driven, serverless document processing pipeline built on AWS that 
automatically classifies documents, extracts structured data, and validates 
submissions using Generative AI without any manual intervention.

![Architecture](docs/images/architecture-diagram.png)

---

## 📋 Overview

This project demonstrates how to combine Amazon Bedrock's Claude 3 Haiku 
model with AWS serverless services to build a production-grade intelligent 
document processing workflow.

**Documents are automatically:**
- Classified into types (Invoice, Contract, Passport, Bank Statement, CV)
- Parsed for structured data extraction using type-specific AI prompts
- Validated for required field completeness
- Archived into processed/ or failed/ folders
- Stored with full results in DynamoDB for querying

---

## 🏗️ Architecture
```
S3 (incoming/) → SQS Queue → Lambda → Amazon Bedrock (Claude 3 Haiku)
                                  ↓                ↓
                             DynamoDB         CloudWatch
                          (store results)    (logs/alerts)
```

**Why this architecture?**
- **S3 → SQS** (not S3 → Lambda directly): SQS buffers high-volume uploads, 
  preventing Lambda concurrency exhaustion
- **Dead Letter Queue**: Failed messages after 3 retries are isolated for 
  investigation without data loss
- **Temperature 0**: Deterministic AI responses ensure the same document 
  always produces the same classification
- **Least-privilege IAM**: Lambda only has permissions it absolutely needs

---

## 🛠️ AWS Services Used

| Service | Purpose |
|---------|---------|
| Amazon Bedrock (Claude 3 Haiku) | Document classification and data extraction |
| AWS Lambda | Serverless processing orchestration |
| Amazon S3 | Document storage (incoming/processed/failed) |
| Amazon SQS | Event buffering and retry management |
| Amazon DynamoDB | Results storage |
| Amazon CloudWatch | Logging, monitoring, and alerting |
| AWS IAM | Least-privilege access control |

---

## 📁 Document Types Supported

| Type | Key Fields Extracted |
|------|---------------------|
| INVOICE | invoiceNumber, vendor, amount, dueDate, lineItems |
| CONTRACT | parties, effectiveDate, expiryDate, governingLaw |
| PASSPORT | fullName, passportNumber, nationality, expiryDate |
| BANK_STATEMENT | accountHolder, statementPeriod, openingBalance, closingBalance |
| CV | fullName, currentRole, yearsExperience, topSkills |
| UNKNOWN | Flagged for manual review |

---

## 🚀 Getting Started

### Prerequisites
- AWS Account with appropriate permissions
- AWS CLI configured (`aws configure`)
- Python 3.12+

### Step 1: Enable Bedrock Model Access
1. Go to AWS Console → Amazon Bedrock → Model Access
2. Request access to **Claude 3 Haiku** (`anthropic.claude-3-haiku-20240307-v1:0`)
3. Wait for approval (usually instant)

### Step 2: Create S3 Bucket
```bash
aws s3 mb s3://your-bucket-name --region eu-west-1
aws s3api put-object --bucket your-bucket-name --key incoming/
aws s3api put-object --bucket your-bucket-name --key processed/
aws s3api put-object --bucket your-bucket-name --key failed/
```

### Step 3: Create SQS Queues
```bash
# Dead Letter Queue first
aws sqs create-queue --queue-name document-processing-dlq

# Main queue with DLQ attached
aws sqs create-queue --queue-name document-processing-queue \
  --attributes '{
    "VisibilityTimeout": "300",
    "RedrivePolicy": "{\"deadLetterTargetArn\":\"YOUR_DLQ_ARN\",\"maxReceiveCount\":\"3\"}"
  }'
```

### Step 4: Create DynamoDB Table
```bash
aws dynamodb create-table \
  --table-name DocumentProcessingResults \
  --attribute-definitions \
    AttributeName=documentId,AttributeType=S \
    AttributeName=timestamp,AttributeType=S \
  --key-schema \
    AttributeName=documentId,KeyType=HASH \
    AttributeName=timestamp,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST
```

### Step 5: Create IAM Role
- Create role `DocumentProcessorLambdaRole`
- Attach `AWSLambdaBasicExecutionRole`
- Add the custom inline policy from `infrastructure/iam-policy.json`

### Step 6: Deploy Lambda
1. Zip the function: `zip function.zip src/lambda/lambda_function.py`
2. Create Lambda function in console with Python 3.12 runtime
3. Set timeout to 3 minutes, memory to 512MB
4. Add SQS trigger pointing to `document-processing-queue`

### Step 7: Connect S3 to SQS
- S3 bucket → Properties → Event Notifications
- Trigger: PUT events on `incoming/` prefix
- Destination: `document-processing-queue`

---

## 🧪 Testing

Upload a test document to trigger the pipeline:
```bash
aws s3 cp test-documents/invoice_test.txt \
  s3://your-bucket-name/incoming/
```

Check results in DynamoDB:
```bash
aws dynamodb scan --table-name DocumentProcessingResults
```

Test all document types at once:
```bash
for f in test-documents/*.txt; do
  aws s3 cp "$f" s3://your-bucket-name/incoming/
  sleep 2
done
```

### Expected DynamoDB Output
```json
{
  "documentId": "incoming/invoice_test.txt",
  "timestamp": "2025-06-15T10:30:00Z",
  "documentType": "INVOICE",
  "confidence": "0.97",
  "extractedData": {
    "invoiceNumber": "INV-2025-0042",
    "vendor": "Nexus Technology Solutions Ltd",
    "amount": "£13,800.00",
    "dueDate": "31 March 2025"
  },
  "validationStatus": "PASSED",
  "processingStatus": "COMPLETE"
}
```

---

## 💡 Key Design Decisions

**Why Claude 3 Haiku over Sonnet?**  
Haiku is ~20x cheaper with sufficient accuracy for structured document 
classification. Sonnet would only be warranted for highly ambiguous or 
complex documents.

**Why `temperature: 0`?**  
Document processing pipelines require deterministic outputs. The same 
invoice must always be classified as INVOICE, not occasionally CONTRACT. 
Temperature 0 eliminates randomness entirely.

**Why separate prompts per document type?**  
Generic extraction prompts produce generic results. Type-specific prompts 
tell the model exactly which fields matter, producing cleaner, more 
consistent JSON output.

**Empty file handling:**  
Empty documents return cleanly without retry — they are bad inputs, not 
transient failures. Retrying them wastes Bedrock tokens and fills the DLQ 
with noise.

---

## 📊 Cost Estimate

For a lab/demo workload (~100 documents):

| Service | Estimated Cost |
|---------|---------------|
| Amazon Bedrock (Claude 3 Haiku) | ~$0.10 – $0.20 |
| Lambda, S3, SQS, DynamoDB | $0.00 (free tier) |
| **Total** | **< $0.25** |

---

## 🔮 Future Enhancements

- [ ] Add Amazon Textract for PDF and image document support
- [ ] Add SNS email notifications on validation failures
- [ ] Build API Gateway endpoint for direct document submission
- [ ] Add confidence threshold — low confidence triggers human review
- [ ] Multi-language document support

---

## 👤 Author

**Gaurav Sharma**  
Technical Solutions Architect | AWS | Generative AI | Financial Services  
[LinkedIn](https://linkedin.com/in/gaurav-sharma-952428158) | 
[GitHub](https://github.com/Sharma1594)

# IDE
.vscode/
.idea/
