
from flask import Flask, render_template, request, redirect, url_for, session, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from openpyxl import Workbook
from db import get_db_connection
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.enums import TA_CENTER
from io import BytesIO
from functools import wraps

app = Flask(__name__)
app.secret_key = "bizvision_secret_key_change_later"


# -------------------- LOGIN PROTECTION --------------------

@app.before_request
def require_login():
    allowed_routes = ["login", "register", "static"]

    if request.endpoint not in allowed_routes:
        if "user_id" not in session:
            return redirect(url_for("login"))


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("role") != "admin":
            return "Access denied: Admin only", 403
        return view(*args, **kwargs)
    return wrapped


# -------------------- REGISTRATION --------------------

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        full_name = request.form["full_name"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        if len(password) < 8:
            return render_template(
                "register.html",
                error="Password must be at least 8 characters."
            )

        password_hash = generate_password_hash(password)

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        try:
            cursor.execute(
                "SELECT user_id FROM users WHERE email = %s",
                (email,)
            )

            if cursor.fetchone():
                return render_template(
                    "register.html",
                    error="This email is already registered."
                )

            cursor.execute("""
                INSERT INTO users
                (company_id, full_name, email, password_hash)
                VALUES (%s, %s, %s, %s)
            """, (1, full_name, email, password_hash))

            connection.commit()

        except Exception as error:
            connection.rollback()
            print("REGISTRATION ERROR:", error)

            return render_template(
                "register.html",
                error="Registration failed. Please try again."
            )

        finally:
            cursor.close()
            connection.close()

        return redirect(url_for("login"))

    return render_template("register.html")


# -------------------- LOGIN --------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None

    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT user_id, full_name, email, password_hash, role
            FROM users
            WHERE email = %s
        """, (email,))

        user = cursor.fetchone()

        cursor.close()
        connection.close()

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["user_id"]
            session["full_name"] = user["full_name"]
            session["role"] = user["role"]

            return redirect(url_for("home"))

        error = "Invalid email or password."

    return render_template("login.html", error=error)


# -------------------- LOGOUT --------------------

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# -------------------- DASHBOARD --------------------

@app.route("/")
def home():
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    cursor.execute("""
        SELECT COUNT(*) AS total_sales,
               COALESCE(SUM(total_amount), 0) AS total_revenue
        FROM sales
    """)
    sales_data = cursor.fetchone()

    cursor.execute("""
        SELECT COALESCE(SUM(amount), 0) AS total_expenses
        FROM expenses
    """)
    expense_data = cursor.fetchone()

    total_sales = sales_data["total_sales"]
    total_revenue = float(sales_data["total_revenue"])
    total_expenses = float(expense_data["total_expenses"])
    net_profit = total_revenue - total_expenses

    cursor.close()
    connection.close()

    return render_template(
        "index.html",
        total_sales=total_sales,
        total_revenue=total_revenue,
        total_expenses=total_expenses,
        net_profit=net_profit
    )


# -------------------- SALES --------------------

@app.route("/sales", methods=["GET", "POST"])
def sales():
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    if request.method == "POST":
        try:
            company_id = int(request.form["company_id"])
            product_id = int(request.form["product_id"])
            quantity = int(request.form["quantity"])
            sale_date = request.form["sale_date"]
            payment_status = request.form["payment_status"]
            customer_id = request.form.get("customer_id") or None

            if quantity <= 0:
                raise ValueError("Quantity must be greater than zero.")

            cursor.execute("""
                SELECT product_id, selling_price,
                       purchase_price, stock_quantity
                FROM products
                WHERE product_id = %s AND company_id = %s
                FOR UPDATE
            """, (product_id, company_id))

            product = cursor.fetchone()

            if not product:
                raise ValueError("Please select a valid product.")

            available_stock = product["stock_quantity"] or 0

            if quantity > available_stock:
                raise ValueError(
                    f"Only {available_stock} units are available."
                )

            selling_price = float(product["selling_price"])
            purchase_price = float(product["purchase_price"])
            total_amount = quantity * selling_price

            cursor.execute("""
                INSERT INTO sales
                (company_id, customer_id, sale_date,
                 total_amount, payment_status)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                company_id, customer_id, sale_date,
                total_amount, payment_status
            ))

            sale_id = cursor.lastrowid

            cursor.execute("""
                INSERT INTO sale_items
                (sale_id, product_id, quantity,
                 selling_price, purchase_price)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                sale_id, product_id, quantity,
                selling_price, purchase_price
            ))

            cursor.execute("""
                UPDATE products
                SET stock_quantity = COALESCE(stock_quantity, 0) - %s
                WHERE product_id = %s
            """, (quantity, product_id))

            connection.commit()

        except Exception as error:
            connection.rollback()
            return f"Sale could not be saved: {error}"

        finally:
            cursor.close()
            connection.close()

        return redirect(url_for("sales", added="success"))

    message = None

    if request.args.get("added") == "success":
        message = "Sale added successfully!"
    elif request.args.get("updated") == "success":
        message = "Sale updated successfully!"
    elif request.args.get("deleted") == "success":
        message = "Sale deleted successfully!"

    cursor.execute("""
        SELECT company_id, company_name
        FROM companies
        ORDER BY company_name
    """)
    companies_data = cursor.fetchall()

    cursor.execute("""
        SELECT product_id, company_id, product_name,
               selling_price, purchase_price, stock_quantity
        FROM products
        ORDER BY product_name
    """)
    products_data = cursor.fetchall()

    search_sale_id = request.args.get("sale_id", "").strip()
    filter_company = request.args.get("company_id", "").strip()
    filter_status = request.args.get("payment_status", "").strip()
    filter_date = request.args.get("sale_date", "").strip()

    query = """
        SELECT s.sale_id, s.company_id, s.customer_id,
               s.sale_date, s.total_amount, s.payment_status,
               c.company_name,
               si.product_id, si.quantity,
               si.selling_price, si.purchase_price,
               p.product_name
        FROM sales s
        LEFT JOIN companies c ON s.company_id = c.company_id
        LEFT JOIN sale_items si ON s.sale_id = si.sale_id
        LEFT JOIN products p ON si.product_id = p.product_id
        WHERE 1=1
    """

    params = []

    if search_sale_id:
        query += " AND CAST(s.sale_id AS CHAR) LIKE %s"
        params.append("%" + search_sale_id + "%")

    if filter_company:
        query += " AND s.company_id = %s"
        params.append(filter_company)

    if filter_status:
        query += " AND s.payment_status = %s"
        params.append(filter_status)

    if filter_date:
        query += " AND s.sale_date = %s"
        params.append(filter_date)

    query += " ORDER BY s.sale_date DESC, s.sale_id DESC"

    cursor.execute(query, tuple(params))
    sales_data = cursor.fetchall()

    cursor.close()
    connection.close()

    return render_template(
        "sales.html",
        sales=sales_data,
        companies=companies_data,
        products=products_data,
        message=message,
        search_sale_id=search_sale_id,
        filter_company=filter_company,
        filter_status=filter_status,
        filter_date=filter_date
    )


# -------------------- EDIT SALE --------------------

@app.route("/sales/edit/<int:sale_id>", methods=["GET", "POST"])
@admin_required
def edit_sale(sale_id):
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        if request.method == "POST":
            company_id = int(request.form["company_id"])
            product_id = int(request.form["product_id"])
            quantity = int(request.form["quantity"])
            sale_date = request.form["sale_date"]
            payment_status = request.form["payment_status"]
            customer_id = request.form.get("customer_id") or None

            if quantity <= 0:
                raise ValueError("Quantity must be greater than zero.")

            cursor.execute("""
                SELECT sale_id
                FROM sales
                WHERE sale_id = %s
                FOR UPDATE
            """, (sale_id,))

            sale = cursor.fetchone()

            if not sale:
                raise ValueError("Sale not found.")

            cursor.execute("""
                SELECT product_id, quantity
                FROM sale_items
                WHERE sale_id = %s
                FOR UPDATE
            """, (sale_id,))

            old_items = cursor.fetchall()

            if len(old_items) != 1:
                raise ValueError(
                    "This sale cannot be edited because it does not "
                    "contain exactly one sale item."
                )

            old_item = old_items[0]

            # Restore previous sale stock inside this transaction.
            cursor.execute("""
                UPDATE products
                SET stock_quantity = COALESCE(stock_quantity, 0) + %s
                WHERE product_id = %s
            """, (old_item["quantity"], old_item["product_id"]))

            cursor.execute("""
                SELECT product_id, selling_price,
                       purchase_price, stock_quantity
                FROM products
                WHERE product_id = %s AND company_id = %s
                FOR UPDATE
            """, (product_id, company_id))

            product = cursor.fetchone()

            if not product:
                raise ValueError("Please select a valid product.")

            available_stock = product["stock_quantity"] or 0

            if quantity > available_stock:
                raise ValueError(
                    f"Only {available_stock} units are available."
                )

            selling_price = float(product["selling_price"])
            purchase_price = float(product["purchase_price"])
            total_amount = quantity * selling_price

            cursor.execute("""
                UPDATE sales
                SET company_id = %s,
                    customer_id = %s,
                    sale_date = %s,
                    total_amount = %s,
                    payment_status = %s
                WHERE sale_id = %s
            """, (
                company_id, customer_id, sale_date,
                total_amount, payment_status, sale_id
            ))

            cursor.execute("""
                UPDATE sale_items
                SET product_id = %s,
                    quantity = %s,
                    selling_price = %s,
                    purchase_price = %s
                WHERE sale_id = %s
            """, (
                product_id, quantity, selling_price,
                purchase_price, sale_id
            ))

            cursor.execute("""
                UPDATE products
                SET stock_quantity = COALESCE(stock_quantity, 0) - %s
                WHERE product_id = %s
            """, (quantity, product_id))

            connection.commit()

            return redirect(url_for("sales", updated="success"))

        cursor.execute("""
            SELECT s.sale_id, s.company_id, s.customer_id,
                   s.sale_date, s.payment_status,
                   si.product_id, si.quantity
            FROM sales s
            JOIN sale_items si ON s.sale_id = si.sale_id
            WHERE s.sale_id = %s
        """, (sale_id,))

        sale = cursor.fetchone()

        if not sale:
            return "Sale not found.", 404

        cursor.execute("""
            SELECT company_id, company_name
            FROM companies
            ORDER BY company_name
        """)
        companies_data = cursor.fetchall()

        cursor.execute("""
            SELECT product_id, company_id, product_name,
                   selling_price, stock_quantity
            FROM products
            ORDER BY product_name
        """)
        products_data = cursor.fetchall()

        return render_template(
            "edit_sale.html",
            sale=sale,
            companies=companies_data,
            products=products_data
        )

    except Exception as error:
        connection.rollback()
        return f"Sale could not be updated: {error}"

    finally:
        cursor.close()
        connection.close()


# -------------------- DELETE SALE --------------------

@app.route("/sales/delete/<int:sale_id>", methods=["POST"])
@admin_required
def delete_sale(sale_id):
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT sale_id
            FROM sales
            WHERE sale_id = %s
            FOR UPDATE
        """, (sale_id,))

        sale = cursor.fetchone()

        if not sale:
            raise ValueError("Sale not found.")

        cursor.execute("""
            SELECT product_id, quantity
            FROM sale_items
            WHERE sale_id = %s
            FOR UPDATE
        """, (sale_id,))

        items = cursor.fetchall()

        for item in items:
            cursor.execute("""
                UPDATE products
                SET stock_quantity = COALESCE(stock_quantity, 0) + %s
                WHERE product_id = %s
            """, (item["quantity"], item["product_id"]))

        cursor.execute("""
            DELETE FROM sale_items
            WHERE sale_id = %s
        """, (sale_id,))

        cursor.execute("""
            DELETE FROM sales
            WHERE sale_id = %s
        """, (sale_id,))

        connection.commit()

    except Exception as error:
        connection.rollback()
        return f"Sale could not be deleted: {error}"

    finally:
        cursor.close()
        connection.close()

    return redirect(url_for("sales", deleted="success"))


# -------------------- COMPANIES --------------------

@app.route("/companies", methods=["GET", "POST"])
@admin_required
def companies():
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    if request.method == "POST":
        company_name = request.form["company_name"].strip()
        email = request.form["email"].strip()
        phone = request.form.get("phone", "").strip() or None
        address = request.form.get("address", "").strip() or None

        cursor.execute("""
            INSERT INTO companies
            (company_name, email, phone, address)
            VALUES (%s, %s, %s, %s)
        """, (company_name, email, phone, address))

        connection.commit()
        cursor.close()
        connection.close()

        return redirect(url_for("companies", added="success"))

    message = None

    if request.args.get("added") == "success":
        message = "Company added successfully!"

    cursor.execute("""
        SELECT * FROM companies
        ORDER BY company_id DESC
    """)
    companies_data = cursor.fetchall()

    cursor.close()
    connection.close()

    return render_template(
        "companies.html",
        companies=companies_data,
        message=message
    )


# -------------------- EXPENSES --------------------

@app.route("/expenses", methods=["GET", "POST"])
@admin_required
def expenses():
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    if request.method == "POST":
        expense_name = request.form["expense_name"].strip()
        category = request.form["category"]
        amount = float(request.form["amount"])
        expense_date = request.form["expense_date"]
        description = request.form.get("description", "").strip() or None

        if amount <= 0:
            cursor.close()
            connection.close()
            return "Expense amount must be greater than zero."

        cursor.execute("""
            INSERT INTO expenses
            (company_id, expense_name, category,
             amount, expense_date, description)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            1, expense_name, category,
            amount, expense_date, description
        ))

        connection.commit()
        cursor.close()
        connection.close()

        return redirect(url_for("expenses", added="success"))

    message = None

    if request.args.get("added") == "success":
        message = "Expense added successfully!"

    cursor.execute("""
        SELECT expense_id, expense_name, category,
               amount, expense_date, description
        FROM expenses
        ORDER BY expense_date DESC
    """)
    expenses_data = cursor.fetchall()

    cursor.close()
    connection.close()

    return render_template(
        "expenses.html",
        expenses=expenses_data,
        message=message
    )


# -------------------- PRODUCTS --------------------

@app.route("/products", methods=["GET", "POST"])
def products():
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    if request.method == "POST":
        company_id = request.form["company_id"]
        product_name = request.form["product_name"].strip()
        purchase_price = float(request.form["purchase_price"])
        selling_price = float(request.form["selling_price"])
        stock_quantity = int(request.form["stock_quantity"])
        category = request.form.get("category", "").strip() or None

        if purchase_price <= 0 or selling_price <= 0 or stock_quantity < 0:
            cursor.close()
            connection.close()
            return "Please enter valid product prices and stock."

        cursor.execute("""
            INSERT INTO products
            (company_id, product_name, category,
             purchase_price, selling_price, stock_quantity)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            company_id, product_name, category,
            purchase_price, selling_price, stock_quantity
        ))

        connection.commit()
        cursor.close()
        connection.close()

        return redirect(url_for("products", added="success"))

    message = None

    if request.args.get("added") == "success":
        message = "Product added successfully!"

    cursor.execute("""
        SELECT company_id, company_name
        FROM companies
    """)
    companies_data = cursor.fetchall()

    cursor.execute("""
        SELECT product_id, product_name, category,
               purchase_price, selling_price, stock_quantity
        FROM products
        ORDER BY product_id DESC
    """)
    products_data = cursor.fetchall()

    cursor.close()
    connection.close()

    return render_template(
        "products.html",
        products=products_data,
        companies=companies_data,
        message=message
    )


# -------------------- CUSTOMERS --------------------

@app.route("/customers", methods=["GET", "POST"])
def customers():
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    if request.method == "POST":
        company_id = request.form["company_id"]
        customer_name = request.form["customer_name"].strip()
        email = request.form.get("email", "").strip() or None
        phone = request.form.get("phone", "").strip() or None
        address = request.form.get("address", "").strip() or None

        cursor.execute("""
            INSERT INTO customers
            (company_id, customer_name, email, phone, address)
            VALUES (%s, %s, %s, %s, %s)
        """, (company_id, customer_name, email, phone, address))

        connection.commit()
        cursor.close()
        connection.close()

        return redirect(url_for("customers", added="success"))

    message = None

    if request.args.get("added") == "success":
        message = "Customer added successfully!"

    cursor.execute("""
        SELECT company_id, company_name
        FROM companies
    """)
    companies_data = cursor.fetchall()

    cursor.execute("""
        SELECT customer_id, customer_name, email, phone, address
        FROM customers
        ORDER BY customer_id DESC
    """)
    customers_data = cursor.fetchall()

    cursor.close()
    connection.close()

    return render_template(
        "customers.html",
        customers=customers_data,
        companies=companies_data,
        message=message
    )


# -------------------- EXPORT REPORT TO EXCEL --------------------

@app.route("/export-excel")
@admin_required
def export_excel():
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    cursor.execute("""
        SELECT COUNT(*) AS total_sales,
               COALESCE(SUM(total_amount), 0) AS total_revenue
        FROM sales
    """)
    sales_data = cursor.fetchone()

    cursor.execute("""
        SELECT COALESCE(SUM(amount), 0) AS total_expenses
        FROM expenses
    """)
    expense_data = cursor.fetchone()

    total_sales = sales_data["total_sales"]
    total_revenue = float(sales_data["total_revenue"])
    total_expenses = float(expense_data["total_expenses"])
    net_profit = total_revenue - total_expenses

    cursor.close()
    connection.close()

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Business Report"

    sheet.append(["BizVision - Business Report"])
    sheet.append(["Metric", "Value"])
    sheet.append(["Total Sales", total_sales])
    sheet.append(["Total Revenue", total_revenue])
    sheet.append(["Total Expenses", total_expenses])
    sheet.append(["Net Profit", net_profit])

    for column in sheet.columns:
        max_length = max(len(str(cell.value or "")) for cell in column)
        sheet.column_dimensions[
            column[0].column_letter
        ].width = max_length + 4

    file = BytesIO()
    workbook.save(file)
    file.seek(0)

    return send_file(
        file,
        as_attachment=True,
        download_name="BizVision_Report.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


# -------------------- EXPORT REPORT TO PDF --------------------

@app.route("/export-pdf")
@admin_required
def export_pdf():
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    cursor.execute("""
        SELECT COUNT(*) AS total_sales,
               COALESCE(SUM(total_amount), 0) AS total_revenue
        FROM sales
    """)
    sales_data = cursor.fetchone()

    cursor.execute("""
        SELECT COALESCE(SUM(amount), 0) AS total_expenses
        FROM expenses
    """)
    expense_summary = cursor.fetchone()

    cursor.execute("""
        SELECT payment_status,
               COUNT(*) AS total_orders,
               COALESCE(SUM(total_amount), 0) AS revenue
        FROM sales
        GROUP BY payment_status
    """)
    payment_report = cursor.fetchall()

    cursor.execute("""
        SELECT category,
               COALESCE(SUM(amount), 0) AS total_amount
        FROM expenses
        GROUP BY category
    """)
    expense_report = cursor.fetchall()

    cursor.close()
    connection.close()

    total_sales = sales_data["total_sales"]
    total_revenue = float(sales_data["total_revenue"])
    total_expenses = float(expense_summary["total_expenses"])
    net_profit = total_revenue - total_expenses

    buffer = BytesIO()
    pdf = SimpleDocTemplate(buffer, pagesize=A4)

    styles = getSampleStyleSheet()
    title_style = styles["Title"]
    title_style.alignment = TA_CENTER

    elements = []

    elements.append(
        Paragraph("BizVision - Business Report", title_style)
    )
    elements.append(Spacer(1, 25))

    summary_data = [
        ["Business Metric", "Value"],
        ["Total Sales", str(total_sales)],
        ["Total Revenue", f"Rs. {total_revenue:.2f}"],
        ["Total Expenses", f"Rs. {total_expenses:.2f}"],
        ["Net Profit", f"Rs. {net_profit:.2f}"]
    ]

    summary_table = Table(summary_data, colWidths=[250, 200])
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2563eb")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 1, colors.grey),
        ("PADDING", (0, 0), (-1, -1), 10)
    ]))

    elements.append(summary_table)
    elements.append(Spacer(1, 25))

    elements.append(
        Paragraph("Payment Status Report", styles["Heading2"])
    )

    payment_data = [
        ["Payment Status", "Total Orders", "Revenue"]
    ]

    for item in payment_report:
        payment_data.append([
            str(item["payment_status"]),
            str(item["total_orders"]),
            f"Rs. {float(item['revenue']):.2f}"
        ])

    payment_table = Table(payment_data, colWidths=[150, 120, 180])
    payment_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2563eb")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 1, colors.grey),
        ("PADDING", (0, 0), (-1, -1), 8)
    ]))

    elements.append(payment_table)
    elements.append(Spacer(1, 25))

    elements.append(
        Paragraph("Expense Category Report", styles["Heading2"])
    )

    expense_data_table = [
        ["Category", "Total Expenses"]
    ]

    for item in expense_report:
        expense_data_table.append([
            str(item["category"] or "Uncategorized"),
            f"Rs. {float(item['total_amount']):.2f}"
        ])

    expense_table = Table(expense_data_table, colWidths=[250, 200])
    expense_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2563eb")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 1, colors.grey),
        ("PADDING", (0, 0), (-1, -1), 8)
    ]))

    elements.append(expense_table)

    pdf.build(elements)
    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name="BizVision_Report.pdf",
        mimetype="application/pdf"
    )


# -------------------- REPORTS --------------------

@app.route("/reports")
@admin_required
def reports():
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    cursor.execute("""
        SELECT COUNT(*) AS total_sales,
               COALESCE(SUM(total_amount), 0) AS total_revenue
        FROM sales
    """)
    sales_data = cursor.fetchone()

    cursor.execute("""
        SELECT COALESCE(SUM(amount), 0) AS total_expenses
        FROM expenses
    """)
    expense_data = cursor.fetchone()

    total_sales = sales_data["total_sales"]
    total_revenue = float(sales_data["total_revenue"])
    total_expenses = float(expense_data["total_expenses"])
    net_profit = total_revenue - total_expenses

    cursor.execute("""
        SELECT payment_status,
               COUNT(*) AS total_orders,
               COALESCE(SUM(total_amount), 0) AS revenue
        FROM sales
        GROUP BY payment_status
    """)
    payment_report = cursor.fetchall()

    cursor.execute("""
        SELECT category,
               COALESCE(SUM(amount), 0) AS total_amount
        FROM expenses
        GROUP BY category
    """)
    expense_report = cursor.fetchall()

    cursor.close()
    connection.close()

    return render_template(
        "reports.html",
        total_sales=total_sales,
        total_revenue=total_revenue,
        total_expenses=total_expenses,
        net_profit=net_profit,
        payment_report=payment_report,
        expense_report=expense_report
    )


# -------------------- MONTHLY & YEARLY ANALYTICS --------------------

@app.route("/analytics")
@admin_required
def analytics():
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    cursor.execute("""
        SELECT
            DATE_FORMAT(sale_date, '%Y-%m') AS period,
            SUM(total_amount) AS revenue
        FROM sales
        GROUP BY DATE_FORMAT(sale_date, '%Y-%m')
        ORDER BY period DESC
    """)
    monthly_sales = cursor.fetchall()

    cursor.execute("""
        SELECT
            YEAR(sale_date) AS period,
            SUM(total_amount) AS revenue
        FROM sales
        GROUP BY YEAR(sale_date)
        ORDER BY period DESC
    """)
    yearly_sales = cursor.fetchall()

    cursor.execute("""
        SELECT
            DATE_FORMAT(expense_date, '%Y-%m') AS period,
            SUM(amount) AS expenses
        FROM expenses
        GROUP BY DATE_FORMAT(expense_date, '%Y-%m')
        ORDER BY period DESC
    """)
    monthly_expenses = cursor.fetchall()

    cursor.execute("""
        SELECT
            YEAR(expense_date) AS period,
            SUM(amount) AS expenses
        FROM expenses
        GROUP BY YEAR(expense_date)
        ORDER BY period DESC
    """)
    yearly_expenses = cursor.fetchall()

    cursor.close()
    connection.close()

    return render_template(
        "analytics.html",
        monthly_sales=monthly_sales,
        yearly_sales=yearly_sales,
        monthly_expenses=monthly_expenses,
        yearly_expenses=yearly_expenses
    )


# -------------------- PROFIT & LOSS --------------------

@app.route("/profit-loss")
@admin_required
def profit_loss():
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    cursor.execute("""
        SELECT COALESCE(SUM(total_amount), 0) AS total_revenue
        FROM sales
    """)
    revenue_data = cursor.fetchone()

    cursor.execute("""
        SELECT COALESCE(SUM(quantity * purchase_price), 0) AS total_cogs
        FROM sale_items
    """)
    cogs_data = cursor.fetchone()

    cursor.execute("""
        SELECT COALESCE(SUM(amount), 0) AS total_expenses
        FROM expenses
    """)
    expense_data = cursor.fetchone()

    total_revenue = float(revenue_data["total_revenue"])
    total_cogs = float(cogs_data["total_cogs"])
    total_expenses = float(expense_data["total_expenses"])

    gross_profit = total_revenue - total_cogs
    net_profit = gross_profit - total_expenses

    cursor.close()
    connection.close()

    return render_template(
        "profit_loss.html",
        total_revenue=total_revenue,
        total_cogs=total_cogs,
        gross_profit=gross_profit,
        total_expenses=total_expenses,
        net_profit=net_profit
    )


# -------------------- SUPPLIERS --------------------

@app.route("/suppliers", methods=["GET", "POST"])
@admin_required
def suppliers():
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    if request.method == "POST":
        company_id = request.form["company_id"]
        supplier_name = request.form["supplier_name"]
        email = request.form["email"]
        phone = request.form["phone"]
        address = request.form["address"]

        cursor.execute("""
            INSERT INTO suppliers
            (company_id, supplier_name, email, phone, address)
            VALUES (%s, %s, %s, %s, %s)
        """, (company_id, supplier_name, email, phone, address))

        connection.commit()
        cursor.close()
        connection.close()

        return redirect(url_for("suppliers"))

    cursor.execute("""
        SELECT * FROM suppliers
        ORDER BY supplier_id DESC
    """)
    supplier_list = cursor.fetchall()

    cursor.execute("""
        SELECT company_id, company_name
        FROM companies
    """)
    company_list = cursor.fetchall()

    cursor.close()
    connection.close()

    return render_template(
        "suppliers.html",
        suppliers=supplier_list,
        companies=company_list
    )


# -------------------- PURCHASES --------------------

@app.route("/purchases", methods=["GET", "POST"])
@admin_required
def purchases():
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    if request.method == "POST":
        try:
            company_id = request.form["company_id"]
            supplier_id = request.form.get("supplier_id") or None
            product_id = request.form["product_id"]
            purchase_date = request.form["purchase_date"]
            quantity = int(request.form["quantity"])
            purchase_price = float(request.form["purchase_price"])
            payment_status = request.form["payment_status"]

            if quantity <= 0 or purchase_price <= 0:
                raise ValueError(
                    "Quantity and price must be greater than zero."
                )

            cursor.execute("""
                SELECT product_id
                FROM products
                WHERE product_id = %s AND company_id = %s
            """, (product_id, company_id))

            product = cursor.fetchone()

            if not product:
                raise ValueError("Please select a valid product.")

            total_amount = quantity * purchase_price

            cursor.execute("""
                INSERT INTO purchases
                (company_id, supplier_id, purchase_date,
                 total_amount, payment_status)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                company_id, supplier_id, purchase_date,
                total_amount, payment_status
            ))

            purchase_id = cursor.lastrowid

            cursor.execute("""
                INSERT INTO purchase_items
                (purchase_id, product_id, quantity, purchase_price)
                VALUES (%s, %s, %s, %s)
            """, (
                purchase_id, product_id, quantity, purchase_price
            ))

            cursor.execute("""
                UPDATE products
                SET stock_quantity = COALESCE(stock_quantity, 0) + %s
                WHERE product_id = %s
            """, (quantity, product_id))

            connection.commit()

        except Exception as error:
            connection.rollback()
            return f"Purchase could not be saved: {error}"

        finally:
            cursor.close()
            connection.close()

        return redirect(url_for("purchases"))

    cursor.execute("""
        SELECT * FROM purchases
        ORDER BY purchase_id DESC
    """)
    purchase_list = cursor.fetchall()

    cursor.execute("""
        SELECT company_id, company_name
        FROM companies
    """)
    company_list = cursor.fetchall()

    cursor.execute("""
        SELECT supplier_id, supplier_name
        FROM suppliers
    """)
    supplier_list = cursor.fetchall()

    cursor.execute("""
        SELECT product_id, product_name
        FROM products
    """)
    product_list = cursor.fetchall()

    cursor.close()
    connection.close()

    return render_template(
        "purchases.html",
        purchases=purchase_list,
        companies=company_list,
        suppliers=supplier_list,
        products=product_list
    )


# -------------------- INVENTORY --------------------

@app.route("/inventory")
def inventory():
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    cursor.execute("""
        SELECT product_id, product_name, category,
               purchase_price, selling_price, stock_quantity
        FROM products
        ORDER BY product_id DESC
    """)

    product_list = cursor.fetchall()

    cursor.close()
    connection.close()

    return render_template(
        "inventory.html",
        products=product_list
    )


# -------------------- RUN APP --------------------

if __name__ == "__main__":
    app.run(debug=True)
