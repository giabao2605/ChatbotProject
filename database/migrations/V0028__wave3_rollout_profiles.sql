-- V0028: Record Wave 3 rollout assignments and tailor the domain profiles
-- used by ingestion, publication validation, and query routing.
-- This migration deliberately does not create owners, approvers, corpus,
-- regression questions, or evaluation gates.

IF NOT EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'V0028')
BEGIN
    ;WITH wave3 AS (
        SELECT * FROM (VALUES
            (N'Production',     3, N'planned', 75),
            (N'Maintenance',    3, N'planned', 75),
            (N'QualityControl', 3, N'planned', 75),
            (N'ISO',            3, N'planned', 75)
        ) v(DeptCode, WaveNumber, RolloutStatus, EvaluationQuestionTarget)
    )
    MERGE dbo.DepartmentRolloutPlan AS target
    USING wave3 AS source ON target.DeptCode = source.DeptCode
    WHEN MATCHED AND target.RolloutStatus = 'planned' THEN UPDATE SET
        WaveNumber = source.WaveNumber,
        EvaluationQuestionTarget = CASE
            WHEN target.EvaluationQuestionTarget < source.EvaluationQuestionTarget
                THEN source.EvaluationQuestionTarget
            ELSE target.EvaluationQuestionTarget
        END,
        UpdatedAt = GETDATE(),
        UpdatedBy = N'V0028 migration'
    WHEN NOT MATCHED THEN INSERT (
        DeptCode, WaveNumber, RolloutStatus, EvaluationQuestionTarget, UpdatedBy
    ) VALUES (
        source.DeptCode, source.WaveNumber, source.RolloutStatus,
        source.EvaluationQuestionTarget, N'V0028 migration'
    );

    ;WITH profiles AS (
        SELECT * FROM (VALUES
            (
                N'Production',
                N'["technical_drawing","bom","work_instruction","production_order","process_sheet","routing_sheet","setup_sheet","quality_record","report","other"]',
                N'["owner_department","shared_departments","domain","document_type","source_system","site","security_level","classification_rationale","classification_model","external_processing_policy"]',
                N'["lệnh sản xuất","quy trình sản xuất","công đoạn sản xuất","định mức sản xuất","sản lượng","tiến độ sản xuất","hướng dẫn công việc"]'
            ),
            (
                N'Maintenance',
                N'["technical_drawing","bom","maintenance_plan","maintenance_schedule","maintenance_record","equipment_manual","inspection_checklist","spare_parts_list","technical_instruction","report","other"]',
                N'["owner_department","shared_departments","domain","document_type","source_system","site","security_level","classification_rationale","classification_model","external_processing_policy"]',
                N'["kế hoạch bảo trì","lịch bảo trì","hồ sơ bảo trì","thiết bị","sự cố thiết bị","phụ tùng","kiểm tra định kỳ","sửa chữa"]'
            ),
            (
                N'QualityControl',
                N'["generic","form","report","procedure","quality_standard","inspection_plan","inspection_record","nonconformance_report","corrective_action","certificate"]',
                N'["owner_department","shared_departments","domain","document_type","source_system","site","security_level","classification_rationale","classification_model","external_processing_policy"]',
                N'["tiêu chuẩn kiểm tra","kết quả đo","lỗi chất lượng","NCR","CAPA","chứng nhận chất lượng","kiểm tra đầu vào","kiểm tra đầu ra"]'
            ),
            (
                N'ISO',
                N'["generic","policy","procedure","work_instruction","form","record","audit_report","nonconformity","corrective_action","management_review","manual"]',
                N'["owner_department","shared_departments","domain","document_type","source_system","site","security_level","classification_rationale","classification_model","external_processing_policy"]',
                N'["ISO 9001","kiểm soát tài liệu","đánh giá nội bộ","điều khoản ISO","hồ sơ ISO","điểm không phù hợp","hành động khắc phục","xem xét lãnh đạo"]'
            )
        ) v(DeptCode, DocumentTypesJson, RequiredMetadataJson, RouterPatternsJson)
    )
    MERGE dbo.DepartmentDomainProfile AS target
    USING profiles AS source ON target.DeptCode = source.DeptCode
    WHEN MATCHED AND target.UpdatedBy IN (N'V0022 migration', N'V0028 migration') THEN UPDATE SET
        DocumentTypesJson = source.DocumentTypesJson,
        RequiredMetadataJson = source.RequiredMetadataJson,
        RouterPatternsJson = source.RouterPatternsJson,
        ParentContextEnabled = 1,
        IsActive = 1,
        UpdatedAt = GETDATE(),
        UpdatedBy = N'V0028 migration'
    WHEN NOT MATCHED THEN INSERT (
        DeptCode, DocumentTypesJson, RequiredMetadataJson, RouterPatternsJson,
        ParentContextEnabled, IsActive, UpdatedBy
    ) VALUES (
        source.DeptCode, source.DocumentTypesJson, source.RequiredMetadataJson,
        source.RouterPatternsJson, 1, 1, N'V0028 migration'
    );

    INSERT INTO dbo._SchemaVersions (Version, Description, AppliedAt)
    VALUES ('V0028', 'Wave 3 rollout assignments and department profiles', GETDATE());
END
GO
