-- V0027: Record Wave 1/Wave 2 rollout assignments and tailor the domain
-- profiles used by ingestion, publication validation, and query routing.
-- This migration deliberately does not create owners, approvers, corpus,
-- regression questions, or evaluation gates.

IF NOT EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'V0027')
BEGIN
    ;WITH wave1 AS (
        SELECT * FROM (VALUES
            (N'Technical', 1, N'pilot', 75),
            (N'HR',        1, N'pilot', 75),
            (N'Purchasing',1, N'pilot', 75)
        ) v(DeptCode, WaveNumber, RolloutStatus, EvaluationQuestionTarget)
    )
    MERGE dbo.DepartmentRolloutPlan AS target
    USING wave1 AS source ON target.DeptCode = source.DeptCode
    WHEN MATCHED AND target.RolloutStatus = 'planned' THEN UPDATE SET
        WaveNumber = source.WaveNumber,
        RolloutStatus = source.RolloutStatus,
        EvaluationQuestionTarget = CASE
            WHEN target.EvaluationQuestionTarget < source.EvaluationQuestionTarget
                THEN source.EvaluationQuestionTarget
            ELSE target.EvaluationQuestionTarget
        END,
        UpdatedAt = GETDATE(),
        UpdatedBy = N'V0027 migration'
    WHEN NOT MATCHED THEN INSERT (
        DeptCode, WaveNumber, RolloutStatus, EvaluationQuestionTarget, UpdatedBy
    ) VALUES (
        source.DeptCode, source.WaveNumber, source.RolloutStatus,
        source.EvaluationQuestionTarget, N'V0027 migration'
    );

    ;WITH wave2 AS (
        SELECT * FROM (VALUES
            (N'Warehouse',  2, N'planned', 75),
            (N'Accountant', 2, N'planned', 75),
            (N'Sales',      2, N'planned', 75),
            (N'Planning',   2, N'planned', 75)
        ) v(DeptCode, WaveNumber, RolloutStatus, EvaluationQuestionTarget)
    )
    MERGE dbo.DepartmentRolloutPlan AS target
    USING wave2 AS source ON target.DeptCode = source.DeptCode
    WHEN MATCHED AND target.RolloutStatus = 'planned' THEN UPDATE SET
        WaveNumber = source.WaveNumber,
        EvaluationQuestionTarget = CASE
            WHEN target.EvaluationQuestionTarget < source.EvaluationQuestionTarget
                THEN source.EvaluationQuestionTarget
            ELSE target.EvaluationQuestionTarget
        END,
        UpdatedAt = GETDATE(),
        UpdatedBy = N'V0027 migration'
    WHEN NOT MATCHED THEN INSERT (
        DeptCode, WaveNumber, RolloutStatus, EvaluationQuestionTarget, UpdatedBy
    ) VALUES (
        source.DeptCode, source.WaveNumber, source.RolloutStatus,
        source.EvaluationQuestionTarget, N'V0027 migration'
    );

    ;WITH profiles AS (
        SELECT * FROM (VALUES
            (
                N'Warehouse',
                N'["generic","form","report","spreadsheet","contract","inventory_report","goods_receipt","goods_issue","stock_card","transfer_form"]',
                N'["owner_department","shared_departments","domain","document_type","source_system","site","security_level","classification_rationale","classification_model","external_processing_policy"]',
                N'["tồn kho","nhập kho","xuất kho","chuyển kho","mã vật tư","phiếu nhập","phiếu xuất","thẻ kho"]'
            ),
            (
                N'Accountant',
                N'["generic","form","report","spreadsheet","contract","invoice","payment_request","ledger","financial_report","payroll","tax_document"]',
                N'["owner_department","shared_departments","domain","document_type","source_system","site","security_level","classification_rationale","classification_model","external_processing_policy"]',
                N'["hóa đơn","thanh toán","công nợ","sổ cái","bảng lương","thuế","báo cáo tài chính","đề nghị thanh toán"]'
            ),
            (
                N'Sales',
                N'["generic","form","report","spreadsheet","contract","quotation","sales_order","invoice","customer_report","revenue_report"]',
                N'["owner_department","shared_departments","domain","document_type","source_system","site","security_level","classification_rationale","classification_model","external_processing_policy"]',
                N'["báo giá","đơn hàng","hợp đồng bán hàng","doanh thu","khách hàng","sales order"]'
            ),
            (
                N'Planning',
                N'["generic","form","report","spreadsheet","contract","production_plan","demand_plan","schedule","material_plan"]',
                N'["owner_department","shared_departments","domain","document_type","source_system","site","security_level","classification_rationale","classification_model","external_processing_policy"]',
                N'["kế hoạch sản xuất","kế hoạch nhu cầu","tiến độ sản xuất","kế hoạch nguyên vật liệu","lịch sản xuất"]'
            )
        ) v(DeptCode, DocumentTypesJson, RequiredMetadataJson, RouterPatternsJson)
    )
    MERGE dbo.DepartmentDomainProfile AS target
    USING profiles AS source ON target.DeptCode = source.DeptCode
    WHEN MATCHED AND target.UpdatedBy IN (N'V0022 migration', N'V0027 migration') THEN UPDATE SET
        DocumentTypesJson = source.DocumentTypesJson,
        RequiredMetadataJson = source.RequiredMetadataJson,
        RouterPatternsJson = source.RouterPatternsJson,
        ParentContextEnabled = 1,
        IsActive = 1,
        UpdatedAt = GETDATE(),
        UpdatedBy = N'V0027 migration'
    WHEN NOT MATCHED THEN INSERT (
        DeptCode, DocumentTypesJson, RequiredMetadataJson, RouterPatternsJson,
        ParentContextEnabled, IsActive, UpdatedBy
    ) VALUES (
        source.DeptCode, source.DocumentTypesJson, source.RequiredMetadataJson,
        source.RouterPatternsJson, 1, 1, N'V0027 migration'
    );

    INSERT INTO dbo._SchemaVersions (Version, Description, AppliedAt)
    VALUES ('V0027', 'Wave 2 rollout assignments and department profiles', GETDATE());
END
GO
