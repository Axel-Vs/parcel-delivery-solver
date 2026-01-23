import pandas as pd

df = pd.read_csv('data/amazon_test_dataset.csv')
df.columns = df.columns.str.strip()
df['Requested Loading Date'] = df['Requested Loading Date'].str.strip()
df['Requested Delivery Date'] = df['Requested Delivery Date'].str.strip()

loading = pd.to_datetime(df['Requested Loading Date'], errors='coerce')
delivery = pd.to_datetime(df['Requested Delivery Date'], errors='coerce')

print("="*60)
print("DATE RANGE ANALYSIS")
print("="*60)

print("\nVENDOR-LEVEL (Requested Loading Date - when we pick up):")
print(f"  MIN: {loading.min()}")
print(f"  MAX: {loading.max()}")

print("\nDEPOT-LEVEL (Requested Delivery Date - when we deliver):")
print(f"  MIN: {delivery.min()}")
print(f"  MAX: {delivery.max()}")

print(f"\nTotal rows (deliveries): {len(df)}")
print(f"Unique vendors: {df['Vendor Name'].nunique()}")

print("\nVendors with multiple deliveries:")
vendor_counts = df['Vendor Name'].value_counts()
multi_delivery = vendor_counts[vendor_counts > 1]
print(f"  Count: {len(multi_delivery)} vendors")
print(f"  Examples:")
for vendor, count in multi_delivery.head(5).items():
    print(f"    - {vendor}: {count} deliveries")

print("\n" + "="*60)
print("CORRECT PERIOD BOUNDS")
print("="*60)

from datetime import timedelta

period_start = loading.min() - timedelta(hours=12)
period_end = delivery.max() + timedelta(hours=12)

print(f"\nPeriod Start = MIN(Loading) - 12h")
print(f"  = {loading.min()} - 12h")
print(f"  = {period_start}")

print(f"\nPeriod End = MAX(Delivery) + 12h")
print(f"  = {delivery.max()} + 12h")
print(f"  = {period_end}")

print("\n" + "="*60)
