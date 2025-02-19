import openai
import gspread
from google.oauth2.service_account import Credentials
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from datetime import datetime
import time
import json
from dotenv import load_dotenv
import os

# Load OpenAI API key from .env
load_dotenv()
client = openai.OpenAI()

# Set up Selenium WebDriver
options = webdriver.ChromeOptions()
# options.add_argument("--headless")  # Uncomment for headless mode
options.add_argument("--start-maximized")
options.add_argument("--disable-gpu")
options.add_argument("--disable-extensions")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--no-sandbox")
driver = webdriver.Chrome(options=options)

# File to track processed bids
PROCESSED_BIDS_FILE = "processed_bids.txt"

# Load processed bids from the file
def load_processed_bids():
    if not os.path.exists(PROCESSED_BIDS_FILE):
        return set()
    with open(PROCESSED_BIDS_FILE, "r") as file:
        return set(line.strip() for line in file.readlines())

# Save a bid title to the processed file
def save_processed_bid(title):
    with open(PROCESSED_BIDS_FILE, "a") as file:
        file.write(f"{title}\n")

# Google Sheets setup
def setup_google_sheets():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file("gcloud_json_key.json", scopes=scope)
    client = gspread.authorize(creds)
    sheet = client.open("CanadaBuysv2")  # Replace with your sheet's name
    return {
        "low": sheet.worksheet("Low"),
        "medium": sheet.worksheet("Medium"),
        "high": sheet.worksheet("High")
    }

# Add a bid to the correct tab
def add_bid_to_sheet(tabs, category, title, bid_url, organization, description, closing_date, email):
    worksheet = tabs.get(category.lower())
    if worksheet:
        worksheet.append_row([title, bid_url, description, organization, closing_date, email])
        print(f"Added bid to {category} tab: {title}")
    else:
        print(f"Unknown category: {category}")

# Check if the closing date is valid
def is_valid_closing_date(closing_date):
    try:
        closing_date_obj = datetime.strptime(closing_date, "%Y/%m/%d")
        return closing_date_obj >= datetime.now()
    except ValueError:
        print(f"Invalid date format: {closing_date}")
        return False

# Check bid relevance using the first assistant
def check_bid_relevance(title, thread_id):
    try:
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=[
                {"type": "text", "text": f"Determine if the following bid title is relevant to our company's areas of interest: general software development, software systems, AI, prison software, UTM, ITS (Traffic), drones, and related technologies. Respond with only 'true' or 'false'.\n\nTitle: {title}"}
            ]
        )
        run = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id="asst_IdXQwLvdq74MEakxEPRaq8th"
        )
        attempts = 0
        while attempts < 30:
            run_status = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
            if run_status.status == "completed":
                messages = client.beta.threads.messages.list(thread_id=thread_id)
                assistant_messages = [msg for msg in messages.data if msg.role == "assistant"]
                if assistant_messages:
                    latest_response = next((block.text.value for block in assistant_messages[0].content if block.type == "text"), None)
                    return latest_response.strip().lower() == "true"
            elif run_status.status == "failed":
                print("Run failed.")
                return False
            time.sleep(2)
            attempts += 1
        return False
    except Exception as e:
        print(f"Error with OpenAI API: {e}")
        return False

# Final qualification using the second assistant
def final_qualification(title, description, thread_id):
    try:
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=[
                {"type": "text", "text": f"Evaluate this bid for final qualification. Dont just put everything as 'high' category. Think critically from our companies perspective. Whats genuinely worth our time and related to our tech. Think from an ai/software startup's perspective. categorize it accordingly (low,medium,high). Refer to your system instructions:\n\nBid Title: {title}\nBid Description: {description}"}
            ]
        )
        run = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id="asst_It8ec0hbjMAnMXOCmXjo9QBs"
        )
        attempts = 0
        while attempts < 30:
            run_status = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
            if run_status.status == "completed":
                messages = client.beta.threads.messages.list(thread_id=thread_id)
                assistant_messages = [msg for msg in messages.data if msg.role == "assistant"]
                if assistant_messages:
                    response = next((block.text.value for block in assistant_messages[0].content if block.type == "text"), None)
                    return json.loads(response)
            elif run_status.status == "failed":
                print("Final qualification run failed.")
                return None
            time.sleep(2)
            attempts += 1
        return None
    except Exception as e:
        print(f"Error with final qualification: {e}")
        return None

# Function to extract the description, organization, and email
def extract_bid_details():
    try:
        # Extract the description
        description_element = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.details-wrapper div.field--name-body"))
        )
        description = description_element.text.strip()

        # Wait for the Contact Information tab and click it
        contact_tab = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "a[href='#edit-group-contact-information']"))
        )
        contact_tab.click()

        # Wait for the email to load
        email_element = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.field--name-field-tender-contact-email .field--item"))
        )
        email = email_element.text.strip()

        # Wait for the organization name to load
        organization_element = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "dd.tender-contact__group-content div.field--name-field-tender-contact-orgname"))
        )
        organization = organization_element.text.strip()

        return description, email, organization

    except Exception as e:
        print(f"Error extracting bid details: {e}")
        return None, None, None


# Main scraping logic
try:
    # Load processed bids
    processed_bids = load_processed_bids()
    bid_index = 0

    url = "https://canadabuys.canada.ca/en/tender-opportunities?search_filter=&category%5B154%5D=154&category%5B156%5D=156&notice_type%5B1681%5D=1681&notice_type%5B1682%5D=1682&notice_type%5B1683%5D=1683&notice_type%5B1684%5D=1684&notice_type%5B1686%5D=1686&notice_type%5B1689%5D=1689&status%5B87%5D=87&location%5B1218%5D=1218&pub%5B3%5D=3&closing%5B2%5D=2&closing%5B3%5D=3&closing%5B4%5D=4&Apply_filters=Apply+filters&record_per_page=50&current_tab=t&words="
    driver.get(url)

    wait = WebDriverWait(driver, 10)
    while True:
        try:
            load_more_button = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "a[rel='next']")))
            load_more_button.click()
            time.sleep(2)
        except Exception:
            break

    thread = client.beta.threads.create()
    final_thread = client.beta.threads.create()
    tabs = setup_google_sheets()

    rows = driver.find_element(By.CSS_SELECTOR, "table.eps-table tbody").find_elements(By.TAG_NAME, "tr")
    for row in rows:
        bid_index += 1
        title = row.find_element(By.CSS_SELECTOR, "td.views-field-dummy-notice-title a").text.strip()

        print(f"Processing Bid {bid_index}: {title}")

        # Skip already processed bids
        if title in processed_bids:
            print(f"Skipping already processed bid: {title}")
            continue

        bid_url = row.find_element(By.CSS_SELECTOR, "td.views-field-dummy-notice-title a").get_attribute("href")
        closing_date = row.find_element(By.CSS_SELECTOR, "td.views-field-field-tender-closing-date").text.strip()

        if not is_valid_closing_date(closing_date):
            print(f"Skipping bid due to invalid closing date: {closing_date}")
            save_processed_bid(title)
            continue

        if not check_bid_relevance(title, thread.id):
            print(f"Irrelevant Bid Skipped: {title}")
            save_processed_bid(title)
            continue

        print(f"Relevant Bid Found: {title}")
        driver.execute_script(f"window.open('{bid_url}', '_blank');")
        driver.switch_to.window(driver.window_handles[-1])

        description, email, organization = extract_bid_details()
        if description:
            response = final_qualification(title, description, final_thread.id)
            if response and response["relevance"]:
                add_bid_to_sheet(tabs, response["category"], title, bid_url, organization, response["description"], closing_date, email)
        save_processed_bid(title)

        driver.close()
        driver.switch_to.window(driver.window_handles[0])

except Exception as e:
    print(f"An error occurred: {e}")
finally:
    driver.quit()
