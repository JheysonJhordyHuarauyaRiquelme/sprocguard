/*
  sprocguard demo database.
  Run it in SSMS, Azure Data Studio or sqlcmd against a DEVELOPMENT SQL Server (2016 or later):
      sqlcmd -S localhost -E -i examples\demo.sql
*/
IF DB_ID(N'DemoShop') IS NULL
    CREATE DATABASE DemoShop;
GO
USE DemoShop;
GO

DROP PROCEDURE IF EXISTS dbo.usp_PlaceOrder;
DROP PROCEDURE IF EXISTS dbo.usp_GetCustomerOrders;
DROP TABLE IF EXISTS dbo.AuditLog;
DROP TABLE IF EXISTS dbo.Orders;
DROP TABLE IF EXISTS dbo.Products;
DROP TABLE IF EXISTS dbo.Customers;
GO

CREATE TABLE dbo.Customers (
    CustomerId int           NOT NULL PRIMARY KEY,
    Name       nvarchar(100) NOT NULL,
    Segment    varchar(20)   NOT NULL
);
CREATE TABLE dbo.Products (
    ProductId int           NOT NULL PRIMARY KEY,
    Name      nvarchar(100) NOT NULL,
    Price     decimal(10,2) NOT NULL,
    Stock     int           NOT NULL
);
CREATE TABLE dbo.Orders (
    OrderId    int IDENTITY(1,1) NOT NULL PRIMARY KEY,
    CustomerId int           NOT NULL REFERENCES dbo.Customers (CustomerId),
    OrderDate  datetime2(0)  NOT NULL,
    Total      decimal(10,2) NOT NULL
);
CREATE TABLE dbo.AuditLog (       -- no primary key on purpose: sprocguard compares it as a multiset
    Action varchar(50)  NOT NULL,
    At     datetime2(3) NOT NULL
);
GO

INSERT dbo.Customers (CustomerId, Name, Segment) VALUES (1, N'Ana', 'VIP'), (2, N'Luis', 'Regular');
INSERT dbo.Products (ProductId, Name, Price, Stock) VALUES (10, N'Café', 12.50, 100), (11, N'Cacao', 8.00, 2);
SET IDENTITY_INSERT dbo.Orders ON;
INSERT dbo.Orders (OrderId, CustomerId, OrderDate, Total)
VALUES (1, 1, '2026-01-05T09:00:00', 25.00), (2, 1, '2026-02-07T15:30:00', 8.00);
SET IDENTITY_INSERT dbo.Orders OFF;
GO

CREATE OR ALTER PROCEDURE dbo.usp_GetCustomerOrders
    @CustomerId int
AS
BEGIN
    SET NOCOUNT ON;
    IF NOT EXISTS (SELECT 1 FROM dbo.Customers WHERE CustomerId = @CustomerId)
        THROW 50001, 'Customer not found', 1;

    SELECT OrderId, OrderDate, Total
    FROM dbo.Orders
    WHERE CustomerId = @CustomerId
    ORDER BY OrderId;
END;
GO

CREATE OR ALTER PROCEDURE dbo.usp_PlaceOrder
    @CustomerId int,
    @ProductId  int,
    @Quantity   int,
    @OrderId    int OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @Price decimal(10,2), @Stock int, @Total decimal(10,2);

    SELECT @Price = Price, @Stock = Stock FROM dbo.Products WHERE ProductId = @ProductId;
    IF @Stock < @Quantity
        THROW 50002, 'Insufficient stock', 1;

    SET @Total = @Price * @Quantity;
    IF EXISTS (SELECT 1 FROM dbo.Customers WHERE CustomerId = @CustomerId AND Segment = 'VIP')
        SET @Total = ROUND(@Total * 0.90, 2);    -- VIP customers get 10% off

    INSERT INTO dbo.Orders (CustomerId, OrderDate, Total) VALUES (@CustomerId, SYSDATETIME(), @Total);
    SET @OrderId = SCOPE_IDENTITY();

    UPDATE dbo.Products SET Stock = Stock - @Quantity WHERE ProductId = @ProductId;
    INSERT INTO dbo.AuditLog (Action, At) VALUES ('order', SYSDATETIME());

    SELECT @OrderId AS OrderId, @Total AS Total;
    RETURN 0;
END;
GO

PRINT 'DemoShop is ready.';
