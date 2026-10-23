# BVGArtts — Art Marketplace

BVGArtts is a responsive art marketplace starter with customer accounts, administrator accounts, a product catalogue, image uploads, pricing, a customer bag, saved orders, and an admin order-management desk.

## What's included

- Customer signup and login (passwords are stored as password hashes, not plain text).
- Three seeded administrator accounts in the included local database.
- Admin dashboard to publish, edit, and delete artworks, set prices, add categories, and upload an image or paste an image URL.
- Customer bag/cart saved in the database.
- Checkout request form that saves the customer, address, items, quantities and order total in the database.
- Admin order list and order-status updates.
- SQLite database for local testing; PostgreSQL support for hosted deployment.
- CSRF protection for forms, session-cookie settings, input checks, and basic upload validation.

## Run locally

You need Python 3.10 or newer.

1. Extract the ZIP file.
2. Open a terminal in the `BVGArtts` folder.
3. Install dependencies:

   ```bash
   python -m venv .venv
   # Windows: .venv\\Scripts\\activate
   # macOS/Linux: source .venv/bin/activate
   pip install -r requirements.txt
   ```

4. Start the website:

   ```bash
   python app.py
   ```

5. Open `http://127.0.0.1:5000` in your browser.

The included `instance/bvgartts.db` database already contains the three admin accounts and six sample artworks. Keep this database file when starting locally. Sample artwork titles, images and prices are examples; replace them with your actual stock and prices from the admin dashboard.

## Admin accounts

The included local database has the three administrator accounts you requested. Their starter login details were provided separately in the setup chat. Usernames are case-insensitive.

The starter passwords are weak. Change each one to a long, unique password before putting the website on the public internet. After signing in, open **Account** in the top navigation to change the password. Keep the local database private: it contains password hashes and should not be committed to a public code repository.

New accounts made using **Create account** have customer permissions only. They cannot post, edit, or delete artwork. Admin status is assigned only during trusted setup.

## Hosted deployment (for example, Render)

For a real public launch, use a managed PostgreSQL database rather than an ephemeral SQLite file. In your hosting dashboard, set:

- `DATABASE_URL` — the private/internal PostgreSQL connection string from your host.
- `SECRET_KEY` — a long random secret value. Do not use the local fallback in a public deployment.
- `COOKIE_SECURE` — `true` when the site is served over HTTPS.
- `ADMIN_USERS_JSON` — only when bootstrapping a new, empty production database. Use a JSON array of objects with `username` and `password`, for example:

  ```json
  [{"username":"venom","password":"REPLACE_WITH_A_LONG_UNIQUE_PASSWORD"},{"username":"Gray","password":"REPLACE_WITH_A_DIFFERENT_LONG_UNIQUE_PASSWORD"},{"username":"Baggy","password":"REPLACE_WITH_ANOTHER_LONG_UNIQUE_PASSWORD"}]
  ```

Use your chosen strong passwords in the hosting dashboard; don't commit real passwords or `.env` files to a public repository. Admin seeding creates missing accounts only: it intentionally does not reset the passwords of existing accounts.

The start command is `gunicorn app:app`. The included `Procfile` contains this command. For uploads on a host that does not preserve local files across deploys (including a typical ephemeral filesystem), attach a persistent disk and set `UPLOAD_DIR` to its mounted `uploads` directory, or use a managed object-storage service. Otherwise uploaded images may disappear after a redeploy. Image URLs hosted elsewhere do not need an upload disk.

## What is not connected yet

- Online payments (Paystack/Flutterwave or another payment provider).
- Automated email/SMS or WhatsApp order notifications.
- Delivery pricing, tracking and fulfilment workflows.
- Email-based password reset and email verification.
- Cloud image storage/CDN and a production backup plan.
- Final legal/contact details, domain, real artist permissions, and actual inventory.

Checkout in this version creates a **pending order request** in the database; it does not take money. The admin team must confirm availability, shipping charges and payment with the customer. Do not advertise payment as automated until a verified payment provider and webhook flow have been connected and tested.

## Database tables

- `user`: customer and admin accounts with hashed passwords and roles.
- `artwork`: titles, artists/suppliers, descriptions, prices, categories and image references.
- `cart_item`: persistent customer bag contents.
- `order`: delivery/contact details, totals, status and order date.
- `order_item`: a snapshot of the artwork title and price at the time the order was placed.
