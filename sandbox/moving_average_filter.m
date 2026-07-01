% filepath: sandbox/moving_average_filter.m

function averages = moving_average_filter(signal, window_size)
    % Check if input signal is a valid 1D array and window size is a positive integer
    if ~isvector(signal) || length(signal) == 0 || ~isscalar(window_size) || window_size <= 0
        error('Invalid input: Signal must be a non-empty 1D array and window size must be a positive integer.');
    end
    
    % Calculate the number of windows that can fit into the signal
    num_windows = length(signal) - window_size + 1;
    
    % Initialize an empty array to store the moving averages
    averages = zeros(1, num_windows);
    
    % Compute the moving average for each window
    for i = 1:num_windows
        averages(i) = mean(signal(i:i+window_size-1));
    end
    
    % Return the computed moving averages
end

% Test script for moving_average_filter
addpath('.');
result = moving_average_filter([1, 2, 3, 4, 5], 2);
assert(abs(result - [2, 3, 4]) < 1e-9, 'Test 1 failed');
disp('All tests passed.');