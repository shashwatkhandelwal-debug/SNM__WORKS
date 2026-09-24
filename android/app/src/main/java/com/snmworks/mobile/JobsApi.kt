package com.snmworks.mobile

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

data class JobItem(
    val id: String,
    val jobNo: String,
    val raisedOn: String?,
    val customerName: String?,
    val product: String,
    val spec: String?,
    val variantId: String?,
    val widthMm: Double?,
    val colour: String?,
    val qtyOrdered: Double,
    val unit: String,
    val qtyProduced: Double,
    val balance: Double,
    val machine: String?,
    val deliveryDue: String?,
    val status: String,
    val remarks: String?
)

data class SpecVariantItem(
    val id: String,
    val designation: String,
    val variantCode: String?,
    val name: String?,
    val specNo: String?,
    val specTitle: String?,
    val status: String
)

data class MatchedCheckItem(
    val id: String,
    val checkNo: String,
    val checkedOn: String?,
    val parameter: String,
    val actual: String?,
    val verdict: String?,
    val source: String
)

data class InspectionPlanItem(
    val parameter: String,
    val stage: String?,
    val limitType: String,
    val specValue: Double?,
    val unit: String?,
    val method: String?,
    val isCritical: Boolean,
    val isChecked: Boolean,
    val latestVerdict: String?,
    val matchedChecks: List<MatchedCheckItem>
)

data class InspectionPlanSummary(
    val totalItems: Int,
    val checkedItems: Int,
    val pendingItems: Int
)

data class InspectionPlanResponse(
    val job: JobItem,
    val selectedVariant: SpecVariantItem?,
    val availableVariants: List<SpecVariantItem>,
    val requiresVariantSelection: Boolean,
    val planItems: List<InspectionPlanItem>,
    val summary: InspectionPlanSummary
)

object JobsApi {
    private val BASE_URL: String
        get() = BuildConfig.API_BASE_URL

    suspend fun fetchJobs(
        accessToken: String,
        status: String? = null,
        query: String? = null
    ): Result<List<JobItem>> = withContext(Dispatchers.IO) {
        try {
            var urlStr = "$BASE_URL/api/v1/jobs?limit=100"
            if (!status.isNullOrBlank()) {
                urlStr += "&status=" + URLEncoder.encode(status, "UTF-8")
            }
            if (!query.isNullOrBlank()) {
                urlStr += "&q=" + URLEncoder.encode(query, "UTF-8")
            }

            val url = URL(urlStr)
            val conn = (url.openConnection() as HttpURLConnection).apply {
                requestMethod = "GET"
                setRequestProperty("Authorization", "Bearer $accessToken")
                setRequestProperty("Accept", "application/json")
                connectTimeout = 10000
                readTimeout = 10000
            }

            val responseCode = conn.responseCode
            if (responseCode !in 200..299) {
                val errStream = conn.errorStream ?: conn.inputStream
                val errText = errStream?.bufferedReader()?.use { it.readText() } ?: "HTTP $responseCode"
                return@withContext Result.failure(Exception("Failed to load jobs: $errText"))
            }

            val body = conn.inputStream.bufferedReader().use { it.readText() }
            val json = JSONObject(body)
            val jobsArray = json.optJSONArray("jobs") ?: JSONArray()
            val list = mutableListOf<JobItem>()

            for (i in 0 until jobsArray.length()) {
                val item = jobsArray.getJSONObject(i)
                list.add(parseJobItem(item))
            }

            Result.success(list)
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    suspend fun fetchInspectionPlan(
        accessToken: String,
        jobId: String,
        variantId: String? = null
    ): Result<InspectionPlanResponse> = withContext(Dispatchers.IO) {
        try {
            var urlStr = "$BASE_URL/api/v1/jobs/$jobId/inspection-plan"
            if (!variantId.isNullOrBlank()) {
                urlStr += "?variant_id=" + URLEncoder.encode(variantId, "UTF-8")
            }

            val url = URL(urlStr)
            val conn = (url.openConnection() as HttpURLConnection).apply {
                requestMethod = "GET"
                setRequestProperty("Authorization", "Bearer $accessToken")
                setRequestProperty("Accept", "application/json")
                connectTimeout = 10000
                readTimeout = 10000
            }

            val responseCode = conn.responseCode
            if (responseCode !in 200..299) {
                val errStream = conn.errorStream ?: conn.inputStream
                val errText = errStream?.bufferedReader()?.use { it.readText() } ?: "HTTP $responseCode"
                return@withContext Result.failure(Exception("Failed to load inspection plan: $errText"))
            }

            val body = conn.inputStream.bufferedReader().use { it.readText() }
            val json = JSONObject(body)

            val jobObj = json.getJSONObject("job")
            val job = parseJobItem(jobObj)

            val selectedVariant = if (json.isNull("selected_variant")) {
                null
            } else {
                parseSpecVariant(json.getJSONObject("selected_variant"))
            }

            val availableVariantsArray = json.optJSONArray("available_variants") ?: JSONArray()
            val availableVariants = mutableListOf<SpecVariantItem>()
            for (i in 0 until availableVariantsArray.length()) {
                availableVariants.add(parseSpecVariant(availableVariantsArray.getJSONObject(i)))
            }

            val planItemsArray = json.optJSONArray("plan_items") ?: JSONArray()
            val planItems = mutableListOf<InspectionPlanItem>()
            for (i in 0 until planItemsArray.length()) {
                val pi = planItemsArray.getJSONObject(i)
                val matchedChecksArray = pi.optJSONArray("matched_checks") ?: JSONArray()
                val matchedChecks = mutableListOf<MatchedCheckItem>()
                for (j in 0 until matchedChecksArray.length()) {
                    val mc = matchedChecksArray.getJSONObject(j)
                    matchedChecks.add(
                        MatchedCheckItem(
                            id = mc.optString("id"),
                            checkNo = mc.optString("check_no"),
                            checkedOn = if (mc.isNull("checked_on")) null else mc.optString("checked_on"),
                            parameter = mc.optString("parameter"),
                            actual = if (mc.isNull("actual")) null else mc.optString("actual"),
                            verdict = if (mc.isNull("verdict")) null else mc.optString("verdict"),
                            source = mc.optString("source", "qc_checks")
                        )
                    )
                }

                planItems.add(
                    InspectionPlanItem(
                        parameter = pi.optString("parameter"),
                        stage = if (pi.isNull("stage")) null else pi.optString("stage"),
                        limitType = pi.optString("limit_type", "nominal"),
                        specValue = if (pi.isNull("spec_value")) null else pi.optDouble("spec_value"),
                        unit = if (pi.isNull("unit")) null else pi.optString("unit"),
                        method = if (pi.isNull("method")) null else pi.optString("method"),
                        isCritical = pi.optBoolean("is_critical", false),
                        isChecked = pi.optBoolean("is_checked", false),
                        latestVerdict = if (pi.isNull("latest_verdict")) null else pi.optString("latest_verdict"),
                        matchedChecks = matchedChecks
                    )
                )
            }

            val summaryObj = json.optJSONObject("summary")
            val summary = InspectionPlanSummary(
                totalItems = summaryObj?.optInt("total_items", planItems.size) ?: planItems.size,
                checkedItems = summaryObj?.optInt("checked_items", 0) ?: 0,
                pendingItems = summaryObj?.optInt("pending_items", planItems.size) ?: planItems.size
            )

            Result.success(
                InspectionPlanResponse(
                    job = job,
                    selectedVariant = selectedVariant,
                    availableVariants = availableVariants,
                    requiresVariantSelection = json.optBoolean("requires_variant_selection", selectedVariant == null),
                    planItems = planItems,
                    summary = summary
                )
            )
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    private fun parseJobItem(item: JSONObject): JobItem {
        val qtyOrdered = item.optDouble("qty_ordered", 0.0)
        val qtyProduced = item.optDouble("qty_produced", 0.0)
        val balance = if (!item.isNull("balance")) item.optDouble("balance") else (qtyOrdered - qtyProduced)

        return JobItem(
            id = item.optString("id"),
            jobNo = item.optString("job_no"),
            raisedOn = if (item.isNull("raised_on")) null else item.optString("raised_on"),
            customerName = if (item.isNull("customer_name")) null else item.optString("customer_name"),
            product = item.optString("product"),
            spec = if (item.isNull("spec")) null else item.optString("spec"),
            variantId = if (item.isNull("variant_id")) null else item.optString("variant_id"),
            widthMm = if (item.isNull("width_mm")) null else item.optDouble("width_mm"),
            colour = if (item.isNull("colour")) null else item.optString("colour"),
            qtyOrdered = qtyOrdered,
            unit = item.optString("unit", "m"),
            qtyProduced = qtyProduced,
            balance = balance,
            machine = if (item.isNull("machine")) null else item.optString("machine"),
            deliveryDue = if (item.isNull("delivery_due")) null else item.optString("delivery_due"),
            status = item.optString("status", "Planned"),
            remarks = if (item.isNull("remarks")) null else item.optString("remarks")
        )
    }

    private fun parseSpecVariant(item: JSONObject): SpecVariantItem {
        return SpecVariantItem(
            id = item.optString("id"),
            designation = item.optString("designation"),
            variantCode = if (item.isNull("variant_code")) null else item.optString("variant_code"),
            name = if (item.isNull("name")) null else item.optString("name"),
            specNo = if (item.isNull("spec_no")) null else item.optString("spec_no"),
            specTitle = if (item.isNull("spec_title")) null else item.optString("spec_title"),
            status = item.optString("status", "Approved")
        )
    }
}
