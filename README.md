# jalopper

An AWS Lambda function that watches a junkyard's online inventory and emails you when a car you're hunting for shows up.

It checks the [Pick-a-Part Jalopy Jungle](https://inventory.pickapartjalopyjungle.com/) inventory for the vehicles you list, at the yards you list, once an hour. It compares each result to what it saw last time (stored in DynamoDB) and sends one email through Amazon SES when anything new appears.

> Not affiliated with Pick-a-Part. This reads a public inventory page; keep the schedule polite.

## How it works

```
EventBridge schedule (every hour)
        |
        v
   Lambda: lambda_function.lambda_handler
        |-- scrapes the inventory page for each (yard, make, model)
        |-- reads the last-seen inventory from DynamoDB
        |-- diffs the two lists
        |-- writes the current inventory back to DynamoDB
        '-- emails any new entries through SES
```

## Repository layout

```
template.yaml            AWS SAM template (function + hourly schedule)
src/
  lambda_function.py     the Lambda handler
  requirements.txt       Python dependencies
```

## Configuration

Edit the two lists at the top of `src/lambda_function.py`:

- `CITIES`: yard name to the site's `YardId` (Garden City, Boise, Caldwell and Nampa are included)
- `VEHICLES`: the `make` and `model` pairs to watch (currently Honda Element and Acura RSX)

Environment variables (`template.yaml` sets them from its parameters, so you normally don't touch them directly):

| Variable | Default | Purpose |
|---|---|---|
| `DYNAMODB_TABLE` | `jalopper-inventory` | Table that stores the last-seen inventory |
| `EMAIL_TO` | none, required | Where notifications are sent |
| `EMAIL_FROM` | none, required | Sender address (must be verified in SES) |

The AWS region comes from Lambda's built-in `AWS_REGION` variable, so you don't set it yourself.

## AWS prerequisites

1. **DynamoDB table** with a string partition key named `city`:
   ```bash
   aws dynamodb create-table \
     --table-name jalopper-inventory \
     --attribute-definitions AttributeName=city,AttributeType=S \
     --key-schema AttributeName=city,KeyType=HASH \
     --billing-mode PAY_PER_REQUEST \
     --region us-west-2
   ```
2. **Amazon SES**: verify the sender address. If your account is still in the SES sandbox, verify the recipient address too.

The IAM permissions (DynamoDB read/write on that table, `ses:SendEmail`) and the environment variables are already defined in `template.yaml`.

## Deploy

Requires the [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html) and AWS credentials.

```bash
sam build
sam deploy --guided
```

`--guided` prompts for the three template parameters: `EmailTo`, `EmailFrom` and `TableName`. Your answers are saved to `samconfig.toml`, which is git-ignored, so your email address never goes into the repo.

`sam build` installs `src/requirements.txt` for you, so don't commit installed packages. To test once without waiting for the schedule:

```bash
sam remote invoke jalopper
```

## Behavior and failure handling

- A failed or timed-out request to the inventory site is skipped, and the stored inventory is left untouched. A bad response can't wipe your baseline and trigger a flood of false alerts.
- If DynamoDB can't be read, that check is skipped for the same reason.
- If a yard returns an empty list after previously having cars, the old baseline is kept until the yard has cars again. The one blind spot: if every car at a yard is sold and an identical entry (same year, model and row) shows up again later, it won't be reported as new.
- The alert email is sent before the new inventory is saved. If SES fails, the run errors out and the same cars are reported on the next run.
- Each inventory request has a 5-second timeout, and the function timeout is 60 seconds.
- A run with no stored data (the first run for a yard) reports everything it finds as new.

## Notes

- The function scrapes the page's HTML table, so it can break if the site changes its markup.
- The schedule is `rate(1 hour)` in `template.yaml`. Change it there.
- `boto3` is included in the Lambda Python runtime, so it is not in `requirements.txt`.
