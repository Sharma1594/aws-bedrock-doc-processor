import json
import boto3
import os
from datetime import datetime

s3_client = boto3.client('s3')
bedrock_client = boto3.client('bedrock-runtime', region_name=os.environ['AWS_REGION_NAME'])
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(os.environ['DYNAMODB_TABLE'])

def lambda_handler(event, context):
    """
    Triggered by SQS. Each record contains an S3 event
    notifying us that a new document was uploaded.
    """
    for record in event['Records']:
        try:
            # Parse the SQS message body (which contains the S3 event)
            body = json.loads(record['body'])
            s3_event = body['Records'][0]

            bucket = s3_event['s3']['bucket']['name']
            key = s3_event['s3']['object']['key']

            print(f"Processing document: s3://{bucket}/{key}")

            # Step 1: Get the document from S3
            document_text = extract_text_from_s3(bucket, key)

            # Step 2: Guard against empty documents — bad input, not a retryable error
            if not document_text or not document_text.strip():
                print(f"SKIPPING: Document is empty - {key}")
                save_results(
                    document_key=key,
                    classification={
                        "documentType": "UNKNOWN",
                        "confidence": 0,
                        "reasoning": "Empty document — no content to process"
                    },
                    extracted_data={},
                    validation={
                        "status": "FAILED",
                        "missingFields": ["document content"],
                        "passed": False
                    }
                )
                move_document(bucket, key, 'failed')
                return  # Exit cleanly — no retry, no DLQ

            # Step 3: Classify the document using Bedrock
            classification = classify_document(document_text)

            # Step 4: Extract structured data based on document type
            extracted_data = extract_information(document_text, classification['documentType'])

            # Step 5: Validate the extracted data
            validation = validate_document(extracted_data, classification['documentType'])

            # Step 6: Save results to DynamoDB
            save_results(key, classification, extracted_data, validation)

            # Step 7: Move document to processed/ folder
            move_document(bucket, key, 'processed')

            print(f"Successfully processed: {key}")
            print(f"Classification: {classification['documentType']} (confidence: {classification['confidence']})")
            print(f"Validation: {validation['status']}")

        except Exception as e:
            print(f"ERROR processing document: {str(e)}")
            # Move to failed/ folder for manual review
            try:
                move_document(bucket, key, 'failed')
            except:
                pass
            raise  # Re-raise so SQS knows to retry / send to DLQ


def extract_text_from_s3(bucket, key):
    """Download document from S3 and return as text."""
    response = s3_client.get_object(Bucket=bucket, Key=key)
    content = response['Body'].read()

    # For this lab, assume text files or pre-extracted text
    # In production you would use Amazon Textract for PDFs and images
    return content.decode('utf-8')


def classify_document(document_text):
    """
    Use Bedrock Claude to classify the document type.
    Returns: { documentType, confidence, reasoning }
    """
    prompt = f"""You are a document classification expert. Analyse the following document and classify it.

Document content:
{document_text[:3000]}

Classify this document into ONE of these categories:
- INVOICE: Bills, payment requests, vendor invoices
- CONTRACT: Legal agreements, terms and conditions, SOWs
- PASSPORT: Identity documents, travel documents
- BANK_STATEMENT: Financial statements, account summaries
- CV: Resumes, curricula vitae, job applications
- UNKNOWN: Cannot be determined

Respond ONLY with valid JSON in this exact format:
{{
    "documentType": "INVOICE",
    "confidence": 0.95,
    "reasoning": "Contains invoice number, line items, and payment terms"
}}"""

    response = bedrock_client.invoke_model(
        modelId='anthropic.claude-3-haiku-20240307-v1:0',
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 200,
            "temperature": 0,  # Deterministic — same input always gives same output
            "messages": [{"role": "user", "content": prompt}]
        })
    )

    result = json.loads(response['body'].read())
    response_text = result['content'][0]['text'].strip()

    # Safe JSON parsing — Bedrock occasionally wraps response in markdown code fences
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        # Strip markdown code fences if present e.g. ```json ... ```
        cleaned = response_text.replace('```json', '').replace('```', '').strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            print(f"WARNING: Could not parse classification response: {response_text}")
            return {
                "documentType": "UNKNOWN",
                "confidence": 0,
                "reasoning": f"Could not parse Bedrock response: {response_text[:200]}"
            }


def extract_information(document_text, document_type):
    """
    Use Bedrock to extract structured fields based on document type.
    Different prompts for different document types.
    """

    # Customise extraction prompt per document type
    extraction_prompts = {
        "INVOICE": """Extract these fields from the invoice:
- invoiceNumber: The invoice ID or reference number
- vendor: Company or person issuing the invoice
- amount: Total amount due (include currency)
- dueDate: Payment due date
- lineItems: List of items/services billed""",

        "CONTRACT": """Extract these fields from the contract:
- parties: All parties involved
- effectiveDate: When the contract starts
- expiryDate: When the contract ends
- keyObligations: Main obligations of each party
- governingLaw: Which jurisdiction governs the contract""",

        "PASSPORT": """Extract these fields from the passport:
- fullName: Full name as shown
- dateOfBirth: Date of birth
- nationality: Nationality
- passportNumber: Document number
- expiryDate: Expiry date""",

        "BANK_STATEMENT": """Extract these fields from the bank statement:
- accountHolder: Account holder name
- accountNumber: Account number (last 4 digits only for security)
- statementPeriod: Period covered
- openingBalance: Opening balance
- closingBalance: Closing balance""",

        "CV": """Extract these fields from the CV:
- fullName: Candidate name
- email: Email address
- currentRole: Most recent job title
- yearsExperience: Total years of experience
- topSkills: Top 5 skills listed"""
    }

    prompt_fields = extraction_prompts.get(document_type, "Extract all key information found in the document.")

    prompt = f"""You are a data extraction expert. Extract structured information from this document.

Document content:
{document_text[:4000]}

{prompt_fields}

Respond ONLY with valid JSON containing the extracted fields. If a field is not found, use null.
Do not include any explanation — JSON only."""

    response = bedrock_client.invoke_model(
        modelId='anthropic.claude-3-haiku-20240307-v1:0',
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 500,
            "temperature": 0,  # Deterministic extraction
            "messages": [{"role": "user", "content": prompt}]
        })
    )

    result = json.loads(response['body'].read())
    response_text = result['content'][0]['text'].strip()

    # Safe JSON parsing with markdown fence stripping
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        cleaned = response_text.replace('```json', '').replace('```', '').strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            print(f"WARNING: Could not parse extraction response: {response_text}")
            return {
                "raw_extraction": response_text[:500],
                "parseError": True
            }


def validate_document(extracted_data, document_type):
    """
    Validate that required fields are present and non-null.
    Returns: { status, missingFields, passed }
    """
    required_fields = {
        "INVOICE": ["invoiceNumber", "vendor", "amount"],
        "CONTRACT": ["parties", "effectiveDate"],
        "PASSPORT": ["fullName", "passportNumber", "expiryDate"],
        "BANK_STATEMENT": ["accountHolder", "statementPeriod"],
        "CV": ["fullName", "currentRole"]
    }

    fields_to_check = required_fields.get(document_type, [])
    missing = [f for f in fields_to_check if not extracted_data.get(f)]

    return {
        "status": "PASSED" if not missing else "FAILED",
        "missingFields": missing,
        "passed": len(missing) == 0
    }


def save_results(document_key, classification, extracted_data, validation):
    """Save all results to DynamoDB."""
    table.put_item(Item={
        "documentId": document_key,
        "timestamp": datetime.utcnow().isoformat(),
        "documentType": classification.get("documentType"),
        "confidence": str(classification.get("confidence", 0)),
        "classificationReasoning": classification.get("reasoning"),
        "extractedData": extracted_data,
        "validationStatus": validation["status"],
        "missingFields": validation["missingFields"],
        "processingStatus": "COMPLETE"
    })


def move_document(bucket, source_key, destination_folder):
    """Move document from incoming/ to processed/ or failed/."""
    filename = source_key.split('/')[-1]
    destination_key = f"{destination_folder}/{filename}"

    # Copy to new location
    s3_client.copy_object(
        Bucket=bucket,
        CopySource={'Bucket': bucket, 'Key': source_key},
        Key=destination_key
    )

    # Delete from original location
    s3_client.delete_object(Bucket=bucket, Key=source_key)
