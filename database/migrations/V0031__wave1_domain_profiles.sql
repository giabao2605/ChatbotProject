-- V0031: Bring the three pilot departments to the same explicit profile
-- standard as Waves 2-4. Preserve every administrator-customized profile.

IF NOT EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'V0031')
BEGIN
    ;WITH profiles AS (
        SELECT * FROM (VALUES
            (
                N'Technical',
                N'["technical_drawing","bom","technical_instruction","material_specification","catalog","report","other"]',
                N'["owner_department","shared_departments","domain","document_type","source_system","site","security_level","classification_rationale","classification_model","external_processing_policy"]',
                N'["bản vẽ kỹ thuật","mã bản vẽ","bảng kê vật tư","BOM","dung sai","vật liệu","hướng dẫn kỹ thuật"]'
            ),
            (
                N'HR',
                N'["generic","policy","procedure","decision","form","contract","payroll","training_record","report"]',
                N'["owner_department","shared_departments","domain","document_type","source_system","site","security_level","classification_rationale","classification_model","external_processing_policy"]',
                N'["nội quy lao động","nhân sự","nghỉ phép","chấm công","bảng lương","bảo hiểm","hợp đồng lao động","đào tạo"]'
            ),
            (
                N'Purchasing',
                N'["generic","form","report","spreadsheet","contract","purchase_order","quotation","invoice","supplier_report"]',
                N'["owner_department","shared_departments","domain","document_type","source_system","site","security_level","classification_rationale","classification_model","external_processing_policy"]',
                N'["mua hàng","đơn mua hàng","purchase order","nhà cung cấp","báo giá","đề nghị mua hàng","hợp đồng mua hàng"]'
            )
        ) v(DeptCode, DocumentTypesJson, RequiredMetadataJson, RouterPatternsJson)
    )
    MERGE dbo.DepartmentDomainProfile AS target
    USING profiles AS source ON target.DeptCode = source.DeptCode
    WHEN MATCHED AND target.UpdatedBy = N'V0022 migration' THEN UPDATE SET
        DocumentTypesJson = source.DocumentTypesJson,
        RequiredMetadataJson = source.RequiredMetadataJson,
        RouterPatternsJson = source.RouterPatternsJson,
        ParentContextEnabled = 1,
        IsActive = 1,
        UpdatedAt = GETDATE(),
        UpdatedBy = N'V0031 migration'
    WHEN NOT MATCHED THEN INSERT (
        DeptCode, DocumentTypesJson, RequiredMetadataJson, RouterPatternsJson,
        ParentContextEnabled, IsActive, UpdatedBy
    ) VALUES (
        source.DeptCode, source.DocumentTypesJson, source.RequiredMetadataJson,
        source.RouterPatternsJson, 1, 1, N'V0031 migration'
    );

    INSERT INTO dbo._SchemaVersions (Version, Description, AppliedAt)
    VALUES ('V0031', 'Wave 1 explicit department domain profiles', GETDATE());
END
GO
