export type {UnresolvedItem} from '../../../shared/api';
export type NormalizationSuggestion={
  skill_id:string;
  skill_name:string;
  category:string|null;
  rank:number;
  lexical_score:number;
  semantic_score:number|null;
  combined_score:number;
  matched_alias:string|null;
  reasons:string[];
  semantic_available:boolean;
};
