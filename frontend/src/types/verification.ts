export interface WebImage {
  url: string;
}

export interface WebEntity {
  description: string;
  score: number;
}

export interface MatchingPage {
  url: string;
  page_title: string;
  full_matching_images: WebImage[];
  partial_matching_images: WebImage[];
}

export interface ReverseSearchResult {
  pages_with_matching_images: MatchingPage[];
  full_matching_images: WebImage[];
  partial_matching_images: WebImage[];
  visually_similar_images: WebImage[];
  web_entities: WebEntity[];
  best_guess_labels: string[];
}

export interface FaceResult {
  bounding_box: [number, number, number, number];
  detection_confidence: number;
  embedding_hash: string;
}

export interface MetadataResult {
  source_url: string;
  canonical_url: string | null;
  title: string | null;
  description: string | null;
  platform: string | null;
  published_at: string | null;
  modified_at: string | null;
  content_type: string | null;
  error: string | null;
}

export interface BlockchainRecord {
  verification_hash: string;
  transaction_hash: string | null;
  block_number: number | null;
  confirmed: boolean;
  explorer_url: string | null;
  duplicate: boolean;
}

export interface VerificationReport {
  status: string;
  faces: FaceResult[];
  reverse_search: ReverseSearchResult | null;
  reverse_search_error: string | null;
  metadata: MetadataResult[];
  metadata_errors: string[];
  blockchain: BlockchainRecord | null;
  blockchain_error: string | null;
  verification_hash: string | null;
  errors: string[];
}

export interface RequestEnvelope {
  blockchain_enabled: boolean;
  contract_address: string | null;
  network: string;
  chain_id: number;
  timeout: number;
}

export interface StageState {
  name: string;
  state: string;
  label: string;
  detail: string;
}

export interface OverallState {
  state: string;
  label: string;
  detail: string;
  issues: string[];
}

export interface VerificationState {
  overall: OverallState;
  stages: StageState[];
}

export interface VerifyResponse {
  request: RequestEnvelope;
  report: VerificationReport;
  verification: VerificationState;
}