/*
  A "harmless refactor" of dbo.usp_PlaceOrder, the kind an AI agent might write.
  It looks cleaner, but it changes how the VIP discount is rounded.
  Deploy it after capturing the baseline, then run: sprocguard verify dbo.usp_PlaceOrder
*/
USE DemoShop;
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

    -- "Simplified": one expression instead of two steps.
    SET @Total = CAST(@Price * @Quantity
                      * CASE WHEN EXISTS (SELECT 1 FROM dbo.Customers
                                          WHERE CustomerId = @CustomerId AND Segment = 'VIP')
                             THEN 0.9 ELSE 1 END AS decimal(10,1));

    INSERT INTO dbo.Orders (CustomerId, OrderDate, Total) VALUES (@CustomerId, SYSDATETIME(), @Total);
    SET @OrderId = SCOPE_IDENTITY();

    UPDATE dbo.Products SET Stock = Stock - @Quantity WHERE ProductId = @ProductId;
    INSERT INTO dbo.AuditLog (Action, At) VALUES ('order', SYSDATETIME());

    SELECT @OrderId AS OrderId, @Total AS Total;
    RETURN 0;
END;
GO
