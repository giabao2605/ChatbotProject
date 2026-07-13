-- V0029: Record the three currently known Wave 4 departments and tailor
-- their domain profiles. The fourth Wave 4 slot remains deliberately empty.
-- This migration does not create owners, approvers, corpus, questions, gates,
-- users, or a placeholder department.

IF NOT EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'V0029')
BEGIN
    ;WITH wave4 AS (
        SELECT * FROM (VALUES
            (N'Molding', 4, N'planned', 75),
            (N'HSE_5S',  4, N'planned', 75),
            (N'IT',      4, N'planned', 75)
        ) v(DeptCode, WaveNumber, RolloutStatus, EvaluationQuestionTarget)
    )
    MERGE dbo.DepartmentRolloutPlan AS target
    USING wave4 AS source ON target.DeptCode = source.DeptCode
    WHEN MATCHED AND target.RolloutStatus = 'planned' THEN UPDATE SET
        WaveNumber = source.WaveNumber,
        EvaluationQuestionTarget = CASE
            WHEN target.EvaluationQuestionTarget < source.EvaluationQuestionTarget
                THEN source.EvaluationQuestionTarget
            ELSE target.EvaluationQuestionTarget
        END,
        UpdatedAt = GETDATE(),
        UpdatedBy = N'V0029 migration'
    WHEN NOT MATCHED THEN INSERT (
        DeptCode, WaveNumber, RolloutStatus, EvaluationQuestionTarget, UpdatedBy
    ) VALUES (
        source.DeptCode, source.WaveNumber, source.RolloutStatus,
        source.EvaluationQuestionTarget, N'V0029 migration'
    );

    ;WITH profiles AS (
        SELECT * FROM (VALUES
            (
                N'Molding',
                N'["technical_drawing","mold_drawing","bom","mold_specification","process_sheet","setup_sheet","maintenance_record","inspection_record","material_specification","other"]',
                N'["owner_department","shared_departments","domain","document_type","source_system","site","security_level","classification_rationale","classification_model","external_processing_policy"]',
                N'["mã khuôn","bản vẽ khuôn","khuôn dập","khuôn ép","cavity","core khuôn","thông số khuôn","vật liệu khuôn","bảo trì khuôn"]'
            ),
            (
                N'HSE_5S',
                N'["generic","policy","procedure","safety_rule","risk_assessment","work_permit","incident_report","emergency_plan","inspection_checklist","training_record","5s_audit","form","report"]',
                N'["owner_department","shared_departments","domain","document_type","source_system","site","security_level","classification_rationale","classification_model","external_processing_policy"]',
                N'["an toàn lao động","đánh giá rủi ro","giấy phép làm việc","sự cố an toàn","ứng phó khẩn cấp","PPE","audit 5S","kiểm tra 5S"]'
            ),
            (
                N'IT',
                N'["generic","policy","procedure","work_instruction","system_guide","network_diagram","asset_inventory","access_request","incident_report","change_record","backup_restore","security_standard","form","report"]',
                N'["owner_department","shared_departments","domain","document_type","source_system","site","security_level","classification_rationale","classification_model","external_processing_policy"]',
                N'["helpdesk","VPN","tài khoản IT","phân quyền hệ thống","sơ đồ mạng","backup restore","sự cố IT","thay đổi hệ thống","tài sản IT"]'
            )
        ) v(DeptCode, DocumentTypesJson, RequiredMetadataJson, RouterPatternsJson)
    )
    MERGE dbo.DepartmentDomainProfile AS target
    USING profiles AS source ON target.DeptCode = source.DeptCode
    WHEN MATCHED AND target.UpdatedBy IN (N'V0022 migration', N'V0029 migration') THEN UPDATE SET
        DocumentTypesJson = source.DocumentTypesJson,
        RequiredMetadataJson = source.RequiredMetadataJson,
        RouterPatternsJson = source.RouterPatternsJson,
        ParentContextEnabled = 1,
        IsActive = 1,
        UpdatedAt = GETDATE(),
        UpdatedBy = N'V0029 migration'
    WHEN NOT MATCHED THEN INSERT (
        DeptCode, DocumentTypesJson, RequiredMetadataJson, RouterPatternsJson,
        ParentContextEnabled, IsActive, UpdatedBy
    ) VALUES (
        source.DeptCode, source.DocumentTypesJson, source.RequiredMetadataJson,
        source.RouterPatternsJson, 1, 1, N'V0029 migration'
    );

    INSERT INTO dbo._SchemaVersions (Version, Description, AppliedAt)
    VALUES ('V0029', 'Wave 4 rollout assignments and department profiles', GETDATE());
END
GO
