from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from fakedb import FakeDatabase, FakeDbError, FakeProc
from sprocguard.config import Config, ConnectionConfig, SafetyConfig, StorageConfig
from sprocguard.db import ResultSet

GET_ORDERS_SQL = """
CREATE PROCEDURE dbo.usp_GetCustomerOrders @CustomerId int
AS
BEGIN
    SET NOCOUNT ON;
    IF NOT EXISTS (SELECT 1 FROM dbo.Customers WHERE CustomerId = @CustomerId)
        THROW 50001, 'Customer not found', 1;
    SELECT OrderId, OrderDate, Total FROM dbo.Orders WHERE CustomerId = @CustomerId ORDER BY OrderId;
END
"""

PLACE_ORDER_SQL = """
CREATE PROCEDURE dbo.usp_PlaceOrder @CustomerId int, @ProductId int, @Quantity int, @OrderId int OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    -- EXEC xp_cmdshell 'dir'  (only a comment: must not be flagged)
    DECLARE @Price decimal(10,2), @Stock int, @Total decimal(10,2);
    SELECT @Price = Price, @Stock = Stock FROM dbo.Products WHERE ProductId = @ProductId;
    IF @Stock < @Quantity
        THROW 50002, 'Insufficient stock', 1;
    SET @Total = @Price * @Quantity;
    IF EXISTS (SELECT 1 FROM dbo.Customers WHERE CustomerId = @CustomerId AND Segment = 'VIP')
        SET @Total = ROUND(@Total * 0.90, 2);
    INSERT INTO dbo.Orders (CustomerId, OrderDate, Total) VALUES (@CustomerId, GETDATE(), @Total);
    SET @OrderId = SCOPE_IDENTITY();
    UPDATE dbo.Products SET Stock = Stock - @Quantity WHERE ProductId = @ProductId;
    INSERT INTO dbo.AuditLog (Action, At) VALUES ('order', SYSDATETIME());
    SELECT @OrderId AS OrderId, @Total AS Total;
    RETURN 0;
END
"""


def money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


def get_orders_body(db: FakeDatabase, args):
    cid = args.get("@CustomerId")
    if not any(c["CustomerId"] == cid for c in db.tables["dbo.Customers"]):
        raise FakeDbError("42000", "[42000] [Microsoft][ODBC Driver 18 for SQL Server][SQL Server]"
                          "Customer not found (50001) (SQLExecDirectW)")
    rows = sorted((o for o in db.tables["dbo.Orders"] if o["CustomerId"] == cid), key=lambda o: o["OrderId"])
    return [ResultSet(["OrderId", "OrderDate", "Total"], [[o["OrderId"], o["OrderDate"], o["Total"]] for o in rows])], 0, {}


def make_place_order_body(vip_discount=Decimal("0.90")):
    def body(db: FakeDatabase, args):
        cid, pid, qty = args["@CustomerId"], args["@ProductId"], args["@Quantity"]
        product = next(p for p in db.tables["dbo.Products"] if p["ProductId"] == pid)
        if product["Stock"] < qty:
            raise FakeDbError("Insufficient stock")
        total = Decimal(product["Price"]) * qty
        if any(c["CustomerId"] == cid and c["Segment"] == "VIP" for c in db.tables["dbo.Customers"]):
            total = total * vip_discount
        db.write()
        order_id = db.next_id()
        db.tables["dbo.Orders"].append({"OrderId": order_id, "CustomerId": cid, "OrderDate": db.now(), "Total": money(total)})
        product["Stock"] -= qty
        db.tables["dbo.AuditLog"].append({"Action": "order", "At": db.now()})
        return [ResultSet(["OrderId", "Total"], [[order_id, money(total)]])], 0, {"@OrderId": order_id}
    return body


def build_db() -> FakeDatabase:
    db = FakeDatabase()
    db.tables = {
        "dbo.Customers": [
            {"CustomerId": 1, "Name": "Ana", "Segment": "VIP"},
            {"CustomerId": 2, "Name": "Luis", "Segment": "Regular"},
        ],
        "dbo.Products": [
            {"ProductId": 10, "Name": "Café", "Price": "12.50", "Stock": 100},
            {"ProductId": 11, "Name": "Cacao", "Price": "8.00", "Stock": 2},
        ],
        "dbo.Orders": [
            {"OrderId": 1, "CustomerId": 1, "OrderDate": "2026-01-05T09:00:00", "Total": "25.00"},
            {"OrderId": 2, "CustomerId": 1, "OrderDate": "2026-02-07T15:30:00", "Total": "8.00"},
        ],
        "dbo.AuditLog": [],
    }
    db.keys = {"dbo.Customers": ["CustomerId"], "dbo.Products": ["ProductId"], "dbo.Orders": ["OrderId"]}
    db.procs = {
        "dbo.usp_getcustomerorders": FakeProc(
            "dbo", "usp_GetCustomerOrders", GET_ORDERS_SQL, [("@CustomerId", "int", False)],
            ["dbo.Customers", "dbo.Orders"], [], get_orders_body,
            first_result_set=[("OrderId", "int"), ("OrderDate", "datetime2(0)"), ("Total", "decimal(10,2)")]),
        "dbo.usp_placeorder": FakeProc(
            "dbo", "usp_PlaceOrder", PLACE_ORDER_SQL,
            [("@CustomerId", "int", False), ("@ProductId", "int", False), ("@Quantity", "int", False),
             ("@OrderId", "int", True)],
            ["dbo.Customers", "dbo.Products"], ["dbo.Orders", "dbo.Products", "dbo.AuditLog"],
            make_place_order_body()),
    }
    return db


@pytest.fixture
def db() -> FakeDatabase:
    return build_db()


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        connection=ConnectionConfig(odbc="Driver=x"),
        safety=SafetyConfig(allowed_databases=["DemoShop"]),
        storage=StorageConfig(),
        root=tmp_path,
    )


PLACE_ORDER_CASES = [
    {"name": "regular customer buys 2", "params": {"@CustomerId": 2, "@ProductId": 10, "@Quantity": 2}},
    {"name": "VIP customer gets 10% off", "params": {"CustomerId": 1, "ProductId": 10, "Quantity": 3}},
    {"name": "insufficient stock raises error", "params": {"@CustomerId": 2, "@ProductId": 11, "@Quantity": 5}},
]
