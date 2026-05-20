"""Risk management for AlgoEngine"""

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, List, Optional, Dict, Any, Callable
from enum import Enum, auto

from ..trading.models import Order, OrderSide
from ..data.models import Symbol
from ..utils.logger import get_logger

if TYPE_CHECKING:
    from ..portfolio.portfolio import Portfolio

logger = get_logger("risk")


class RiskRuleType(Enum):
    """Types of risk rules"""
    POSITION_SIZE_LIMIT = auto()
    EXPOSURE_LIMIT = auto()
    DRAWDOWN_LIMIT = auto()
    CONCENTRATION_LIMIT = auto()
    VOLATILITY_LIMIT = auto()


@dataclass
class RiskRule:
    """Individual risk rule configuration"""
    rule_type: RiskRuleType
    max_value: Optional[Decimal] = None
    max_percent: Optional[float] = None
    enabled: bool = True
    
    def check(self, context: 'RiskContext') -> tuple[bool, str]:
        """Check if rule is satisfied, returns (passed, reason)"""
        return True, ""


@dataclass
class RiskContext:
    """Context for risk checks"""
    portfolio: 'Portfolio'
    order: Optional[Order] = None
    symbol: Optional[Symbol] = None
    proposed_quantity: Optional[Decimal] = None
    current_price: Optional[Decimal] = None


class RiskManager:
    """Manage trading risk"""
    
    def __init__(self, portfolio: 'Portfolio') -> None:
        self._portfolio = portfolio
        self._rules: List[RiskRule] = []
        self._custom_checks: List[Callable[[RiskContext], tuple[bool, str]]] = []
        
        # Default risk parameters
        self._max_position_size_percent = 10.0  # Max 10% per position
        self._max_total_exposure_percent = 100.0  # Max 100% exposed
        self._max_drawdown_percent = 20.0  # Stop trading at 20% drawdown
        self._max_concentration_percent = 25.0  # Max 25% in single symbol
        
        logger.info("RiskManager initialized")
    
    @property
    def max_position_size_percent(self) -> float:
        """Get max position size as percentage of portfolio"""
        return self._max_position_size_percent
    
    @max_position_size_percent.setter
    def max_position_size_percent(self, value: float) -> None:
        """Set max position size percentage"""
        self._max_position_size_percent = value
    
    @property
    def max_drawdown_percent(self) -> float:
        """Get max drawdown before trading halt"""
        return self._max_drawdown_percent
    
    @max_drawdown_percent.setter
    def max_drawdown_percent(self, value: float) -> None:
        """Set max drawdown percentage"""
        self._max_drawdown_percent = value
    
    def add_rule(self, rule: RiskRule) -> None:
        """Add a risk rule"""
        self._rules.append(rule)
    
    def add_custom_check(self, check: Callable[[RiskContext], tuple[bool, str]]) -> None:
        """Add custom risk check function"""
        self._custom_checks.append(check)
    
    def check_order(self, order: Order, current_price: Decimal) -> tuple[bool, str]:
        """Check if order passes all risk rules"""
        context = RiskContext(
            portfolio=self._portfolio,
            order=order,
            symbol=order.symbol,
            proposed_quantity=order.quantity,
            current_price=current_price
        )
        
        # Check drawdown limit
        if not self._check_drawdown_limit():
            return False, f"Trading halted: drawdown exceeds {self._max_drawdown_percent}%"
        
        # Check position size limit
        passed, reason = self._check_position_size(context)
        if not passed:
            return False, reason
        
        # Check concentration limit
        passed, reason = self._check_concentration_limit(context)
        if not passed:
            return False, reason
        
        # Check exposure limit
        passed, reason = self._check_exposure_limit(context)
        if not passed:
            return False, reason
        
        # Check buying power
        passed, reason = self._check_buying_power(context)
        if not passed:
            return False, reason
        
        # Check custom rules
        for rule in self._rules:
            if rule.enabled:
                passed, reason = rule.check(context)
                if not passed:
                    return False, reason
        
        # Check custom functions
        for check in self._custom_checks:
            passed, reason = check(context)
            if not passed:
                return False, reason
        
        return True, "Risk check passed"
    
    def _check_drawdown_limit(self) -> bool:
        """Check if drawdown is within limits"""
        if self._portfolio.initial_cash == 0:
            return True
        
        current_value = self._portfolio.total_value
        initial_value = self._portfolio.initial_cash
        
        # Calculate current drawdown from peak
        # For simplicity, we use initial value as peak
        # In production, track actual peak
        drawdown = (initial_value - current_value) / initial_value * 100
        
        return drawdown < self._max_drawdown_percent
    
    def _check_position_size(self, context: RiskContext) -> tuple[bool, str]:
        """Check if position size is within limits"""
        if not context.current_price or not context.proposed_quantity:
            return True, ""
        
        position_value = context.proposed_quantity * context.current_price
        portfolio_value = self._portfolio.total_value
        
        if portfolio_value == 0:
            return True, ""
        
        position_percent = float(position_value) / float(portfolio_value) * 100
        
        if position_percent > self._max_position_size_percent:
            return False, (
                f"Position size {position_percent:.1f}% exceeds limit "
                f"{self._max_position_size_percent}%"
            )
        
        return True, ""
    
    def _check_concentration_limit(self, context: RiskContext) -> tuple[bool, str]:
        """Check symbol concentration limit"""
        if not context.symbol or not context.current_price:
            return True, ""
        
        # Get current position for this symbol
        current_position = self._portfolio.get_position(context.symbol)
        current_quantity = current_position.quantity if current_position else Decimal("0")
        
        # Calculate total exposure to this symbol
        if context.order:
            if context.order.side == OrderSide.BUY:
                total_quantity = current_quantity + context.proposed_quantity
            else:
                # Short selling - separate calculation
                total_quantity = current_quantity
        else:
            total_quantity = context.proposed_quantity
        
        total_value = total_quantity * context.current_price
        portfolio_value = self._portfolio.total_value
        
        if portfolio_value == 0:
            return True, ""
        
        concentration = float(total_value) / float(portfolio_value) * 100
        
        if concentration > self._max_concentration_percent:
            return False, (
                f"Concentration in {context.symbol.ticker} {concentration:.1f}% "
                f"exceeds limit {self._max_concentration_percent}%"
            )
        
        return True, ""
    
    def _check_exposure_limit(self, context: RiskContext) -> tuple[bool, str]:
        """Check total portfolio exposure"""
        if not context.current_price or not context.proposed_quantity:
            return True, ""
        
        new_position_value = context.proposed_quantity * context.current_price
        current_exposure = self._portfolio.positions_value
        total_exposure = current_exposure + new_position_value
        
        portfolio_value = self._portfolio.total_value
        if portfolio_value == 0:
            return True, ""
        
        exposure_percent = float(total_exposure) / float(portfolio_value) * 100
        
        if exposure_percent > self._max_total_exposure_percent:
            return False, (
                f"Total exposure {exposure_percent:.1f}% exceeds limit "
                f"{self._max_total_exposure_percent}%"
            )
        
        return True, ""
    
    def _check_buying_power(self, context: RiskContext) -> tuple[bool, str]:
        """Check if sufficient buying power"""
        if not context.current_price or not context.proposed_quantity:
            return True, ""
        
        order_value = context.proposed_quantity * context.current_price
        buying_power = self._portfolio.get_buying_power()
        
        if order_value > buying_power:
            return False, (
                f"Insufficient buying power: ${buying_power:.2f} available, "
                f"${order_value:.2f} required"
            )
        
        return True, ""
    
    def calculate_position_size(
        self,
        symbol: Symbol,
        price: Decimal,
        risk_per_trade_percent: float = 1.0,
        stop_loss_percent: float = 2.0
    ) -> Decimal:
        """Calculate position size based on risk per trade"""
        portfolio_value = self._portfolio.total_value
        
        # Risk amount per trade
        risk_amount = portfolio_value * Decimal(str(risk_per_trade_percent / 100))
        
        # Risk per share
        risk_per_share = price * Decimal(str(stop_loss_percent / 100))
        
        if risk_per_share == 0:
            return Decimal("0")
        
        # Calculate shares
        shares = risk_amount / risk_per_share
        
        # Round down to whole shares
        shares = Decimal(int(shares))
        
        logger.info(
            f"Position size for {symbol.ticker}: {shares} shares "
            f"(${float(shares * price):.2f})"
        )
        
        return shares
    
    def get_risk_summary(self) -> Dict[str, Any]:
        """Get current risk metrics summary"""
        portfolio_value = self._portfolio.total_value
        initial_value = self._portfolio.initial_cash
        
        drawdown = 0.0
        if initial_value > 0:
            drawdown = float(initial_value - portfolio_value) / float(initial_value) * 100
        
        # Get exposure by symbol
        exposure = self._portfolio.get_exposure_by_symbol()
        max_concentration = max(exposure.values()) if exposure else 0.0
        
        return {
            'current_drawdown': drawdown,
            'drawdown_limit': self._max_drawdown_percent,
            'trading_halted': drawdown >= self._max_drawdown_percent,
            'total_exposure': float(self._portfolio.positions_value / portfolio_value * 100) if portfolio_value > 0 else 0,
            'exposure_limit': self._max_total_exposure_percent,
            'max_concentration': float(max_concentration),
            'concentration_limit': self._max_concentration_percent,
            'cash_available': float(self._portfolio.cash)
        }
