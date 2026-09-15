import boto3
import datetime

ce = boto3.client("ce", region_name="us-east-1")
today = datetime.date.today()
start = (today - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
end = (today + datetime.timedelta(days=1)).strftime("%Y-%m-%d")

try:
    resp = ce.get_cost_and_usage(
        TimePeriod={"Start": start, "End": end},
        Granularity="DAILY",
        Filter={"Dimensions": {"Key": "SERVICE", "Values": ["Amazon Textract"]}},
        Metrics=["UnblendedCost"]
    )
    print("Textract Daily Costs from AWS CE:")
    total_ce = 0.0
    for day in resp.get("ResultsByTime", []):
        cost = float(day["Total"]["UnblendedCost"]["Amount"])
        total_ce += cost
        print(f"  {day['TimePeriod']['Start']}: ${cost:.4f}")
    print(f"Total Textract AWS Incurred: ${total_ce:.4f}")
except Exception as e:
    print("Could not query CE:", e)
